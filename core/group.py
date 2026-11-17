"""
Boundary detection for full shoot mode (docs/V2-PLAN.md §7).

Turns one ordered shoot into contiguous groups ("sets") by making one decision
per adjacent pair of photos: boundary or not. This is a linear pass, not
clustering, and every boundary records why it fired.

This is the Python reference implementation. The phone will carry a JS port
(frontend/src/lib/group.js, Phase B). Both must reproduce
tests/fixtures/grouping_golden.json exactly, so everything a port needs is
pinned here.

Features per photo
------------------
  captured_at_ms  EXIF DateTimeOriginal (+ SubSecTimeOriginal), as milliseconds.
                  Parsed as if UTC: only differences between frames are used.
  time_source     "exif_subsec" | "exif" | "file_mtime" | "none".
                  This module only ever produces the EXIF sources or "none";
                  a file's mtime on a laptop or Pi is a copy time. The decision
                  code still accepts "file_mtime" so the phone can supply it.
  dhash           16 hex chars. Source image -> 64x64 (area average) ->
                  luma (299R + 587G + 114B + 500) // 1000 -> 9 wide x 8 high
                  (area average). Bit = left pixel > right pixel, row-major,
                  most significant bit first.
  hist            256 hex chars = 128 bytes. From the same 64x64 image:
                  HSV with 8 hue x 4 saturation x 4 value bins, bin index
                  h*16 + s*4 + v. Counts are quantised to bytes as
                  (2*255*count + max_count) // (2*max_count), so the fullest
                  bin is 255.

Distances per adjacent pair
---------------------------
  dt  seconds between capture times. Valid only when both times come from the
      same clock (both EXIF, or both file_mtime); otherwise None.
  dh  Hamming distance of the two dhashes, 0-64.
  dc  1 - histogram intersection of the two L1-normalised histograms, 0-1.

Where this departs from the plan (docs/V2-REVIEW.md G1, G2, G3)
---------------------------------------------------------------
  - Ordering: by (capture time, filename) only if every photo has an EXIF
    time; otherwise by filename for the whole shoot.
  - Missing data: no time -> "visual_only" rule; no fingerprint -> time only;
    neither -> no boundary.
  - Post-pass: merge small groups first, then split oversized ones. The split
    never leaves a piece smaller than MIN_GROUP.
  - FAST_RULE selects how rule 4 combines dh and dc. Default "and" is the
    plan's behaviour.

No GPS or other EXIF tag is read. Nothing here is logged or persisted.
"""

from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

GROUPING_VERSION = "1"

THRESHOLDS: dict = {
    "T_SOFT":    20,      # s. Below this, frames are presumed same-set.
    "T_HARD":    90,      # s. At or above this, presumed a new set.
    "H_SAME":    6,       # dHash distance for near-identical framing.
    "C_SAME":    0.08,    # histogram distance for the same scene colours.
    "H_LOW":     14,      # moderate structural change.
    "C_LOW":     0.20,    # moderate colour change.
    "H_HIGH":    24,      # strong structural change.
    "C_HIGH":    0.35,    # strong colour change.
    "MIN_GROUP": 2,       # groups smaller than this are merged into a neighbour.
    "MAX_GROUP": 20,      # larger groups are split. Also the one-call Gemini cap.
    "FAST_RULE": "and",   # rule 4 combinator: "and" | "or" | "dh".
}

FAST_RULES = ("and", "or", "dh")

_EXIF_IFD            = 0x8769
_TAG_DATETIME_ORIG   = 0x9003
_TAG_SUBSEC_ORIG     = 0x9291
_EXIF_SOURCES        = ("exif_subsec", "exif")
_FINGERPRINT_SIZE    = 64
_HIST_BINS           = 128


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

def resolve_thresholds(overrides: dict | None = None) -> dict:
    """Return THRESHOLDS with overrides applied. Raises ValueError if invalid."""
    th = dict(THRESHOLDS)
    if overrides:
        unknown = set(overrides) - set(THRESHOLDS)
        if unknown:
            raise ValueError(f"Unknown grouping thresholds: {sorted(unknown)}")
        th.update(overrides)
    if th["FAST_RULE"] not in FAST_RULES:
        raise ValueError(f"FAST_RULE must be one of {FAST_RULES}, got {th['FAST_RULE']!r}")
    if not 0 <= th["T_SOFT"] <= th["T_HARD"]:
        raise ValueError("Thresholds need 0 <= T_SOFT <= T_HARD")
    if th["MIN_GROUP"] < 1:
        raise ValueError("MIN_GROUP must be at least 1")
    if th["MAX_GROUP"] < 2 * th["MIN_GROUP"]:
        raise ValueError("MAX_GROUP must be at least 2 x MIN_GROUP so a split can be legal")
    return th


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def read_capture_time(path: str | Path) -> tuple[int | None, str]:
    """
    Read EXIF capture time from an original file.

    Returns (milliseconds, time_source). Only DateTimeOriginal and
    SubSecTimeOriginal are read. Returns (None, "none") when unavailable.
    """
    try:
        with Image.open(path) as im:
            ifd = im.getexif().get_ifd(_EXIF_IFD)
    except (OSError, ValueError, SyntaxError):
        return None, "none"

    raw = ifd.get(_TAG_DATETIME_ORIG)
    if not isinstance(raw, str):
        return None, "none"
    try:
        dt = datetime.strptime(raw.strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, "none"
    ms = int(dt.replace(tzinfo=UTC).timestamp()) * 1000

    subsec = str(ifd.get(_TAG_SUBSEC_ORIG, "")).strip()
    if subsec.isdigit():
        # "34" means .34 s, "5" means .5 s. Keep millisecond precision.
        return ms + int(subsec.ljust(3, "0")[:3]), "exif_subsec"
    return ms, "exif"


def dhash_from_bgr(small_bgr: np.ndarray) -> str:
    """64-bit difference hash of a 64x64 BGR image, as 16 hex chars."""
    b = small_bgr[:, :, 0].astype(np.int64)
    g = small_bgr[:, :, 1].astype(np.int64)
    r = small_bgr[:, :, 2].astype(np.int64)
    luma = ((299 * r + 587 * g + 114 * b + 500) // 1000).astype(np.float32)
    grid = cv2.resize(luma, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (grid[:, :-1] > grid[:, 1:]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hist_from_bgr(small_bgr: np.ndarray) -> str:
    """Quantised 8x4x4 HSV histogram of a 64x64 BGR image, as 256 hex chars."""
    px = small_bgr.reshape(-1, 3).astype(np.int64)
    b, g, r = px[:, 0], px[:, 1], px[:, 2]
    mx    = px.max(axis=1)
    mn    = px.min(axis=1)
    delta = mx - mn

    v_bin = mx >> 6
    s_bin = np.where(mx > 0, np.minimum(4 * delta // np.maximum(mx, 1), 3), 0)

    d   = np.maximum(delta, 1).astype(np.float64)
    hue = np.zeros(len(px), dtype=np.float64)   # degrees; 0 where delta == 0
    is_r = (delta > 0) & (mx == r)
    is_g = (delta > 0) & (mx == g) & ~is_r
    is_b = (delta > 0) & ~is_r & ~is_g
    hue[is_r] = (60.0 * ((g - b) / d))[is_r] % 360.0
    hue[is_g] = (60.0 * ((b - r) / d) + 120.0)[is_g]
    hue[is_b] = (60.0 * ((r - g) / d) + 240.0)[is_b]
    h_bin = np.floor(hue / 45.0).astype(np.int64) % 8

    counts = np.bincount(h_bin * 16 + s_bin * 4 + v_bin, minlength=_HIST_BINS)
    top    = int(counts.max())
    quant  = (2 * 255 * counts + top) // (2 * top)
    return bytes(int(q) for q in quant).hex()


def compute_fingerprint(image_path: str | Path) -> tuple[str | None, str | None]:
    """
    (dhash, hist) for an image file, or (None, None) if it cannot be decoded.

    Call this on the compressed ~1.5 MP copy, which is what the phone
    fingerprints.
    """
    bgr = cv2.imread(str(image_path))
    if bgr is None:
        return None, None
    small = cv2.resize(
        bgr, (_FINGERPRINT_SIZE, _FINGERPRINT_SIZE), interpolation=cv2.INTER_AREA
    )
    return dhash_from_bgr(small), hist_from_bgr(small)


def extract_features(photos: list[dict]) -> list[dict]:
    """
    Build feature records for ingested photos.

    Args:
        photos: ingest() output. Capture time is read from "original_path"
                (compression strips EXIF); the fingerprint from "path".
    """
    features = []
    for photo in photos:
        captured_at_ms, time_source = read_capture_time(photo["original_path"])
        dhash, hist = compute_fingerprint(photo["path"])
        features.append({
            "photo_id":       photo["photo_id"],
            "captured_at_ms": captured_at_ms,
            "time_source":    time_source,
            "dhash":          dhash,
            "hist":           hist,
        })
    return features


# ---------------------------------------------------------------------------
# Pair distances
# ---------------------------------------------------------------------------

def hamming(dhash_a: str, dhash_b: str) -> int:
    return (int(dhash_a, 16) ^ int(dhash_b, 16)).bit_count()


def hist_distance(hist_a: str, hist_b: str) -> float:
    """1 - intersection of the two L1-normalised histograms. 0 = identical."""
    a, b = bytes.fromhex(hist_a), bytes.fromhex(hist_b)
    sum_a, sum_b = sum(a), sum(b)
    if sum_a == 0 or sum_b == 0:
        return 1.0
    # Integer numerator so Python and JS agree to the last bit.
    overlap = sum(min(x * sum_b, y * sum_a) for x, y in zip(a, b))
    return 1.0 - overlap / (sum_a * sum_b)


def _clock(feature: dict) -> str | None:
    if feature.get("captured_at_ms") is None:
        return None
    source = feature.get("time_source")
    if source in _EXIF_SOURCES:
        return "exif"
    if source == "file_mtime":
        return "mtime"
    return None


def pair_distances(a: dict, b: dict) -> tuple[float | None, int | None, float | None]:
    """(dt, dh, dc) for two adjacent photos. Any of them may be None."""
    dt = None
    clock = _clock(a)
    if clock is not None and clock == _clock(b):
        dt = abs(b["captured_at_ms"] - a["captured_at_ms"]) / 1000

    dh = dc = None
    if a.get("dhash") and b.get("dhash") and a.get("hist") and b.get("hist"):
        dh = hamming(a["dhash"], b["dhash"])
        dc = hist_distance(a["hist"], b["hist"])
    return dt, dh, dc


# ---------------------------------------------------------------------------
# Decision table (§7.3, completed for missing data per §7.4 / review G1)
# ---------------------------------------------------------------------------

def decide_pair(
    dt: float | None,
    dh: int | None,
    dc: float | None,
    thresholds: dict | None = None,
) -> tuple[bool, str | None, str]:
    """
    Decide whether a boundary sits between two adjacent photos.

    Returns (boundary, reason, rule). `reason` is None when there is no
    boundary. `rule` names the table row that decided, for tests and
    diagnostics: "1".."5", "visual_only", "time_only", or "no_data".
    """
    th = thresholds if thresholds is not None else THRESHOLDS
    has_time   = dt is not None
    has_visual = dh is not None and dc is not None

    if not has_time and not has_visual:
        return False, None, "no_data"

    if not has_visual:
        if dt >= th["T_HARD"]:
            return True, "time_gap", "time_only"
        return False, None, "time_only"

    if not has_time:
        if dh >= th["H_LOW"] and dc >= th["C_LOW"]:
            return True, "visual_only", "visual_only"
        return False, None, "visual_only"

    if dt >= th["T_HARD"]:
        if dh <= th["H_SAME"] and dc <= th["C_SAME"]:
            return False, None, "2"      # long pause, same set resumed
        return True, "time_gap", "1"

    if dt >= th["T_SOFT"]:
        if dh >= th["H_LOW"] or dc >= th["C_LOW"]:
            return True, "time+visual", "3"
        return False, None, "5"

    fast_rule = th["FAST_RULE"]
    if fast_rule == "and":
        fired = dh >= th["H_HIGH"] and dc >= th["C_HIGH"]
    elif fast_rule == "or":
        fired = dh >= th["H_HIGH"] or dc >= th["C_HIGH"]
    else:
        fired = dh >= th["H_HIGH"]
    if fired:
        return True, "visual_change", "4"
    return False, None, "5"


# ---------------------------------------------------------------------------
# Ordering and grouping
# ---------------------------------------------------------------------------

def order_photos(features: list[dict]) -> tuple[list[dict], str]:
    """
    Put photos in shooting order.

    Returns (ordered, order) where order is "capture_time" when every photo
    has an EXIF time, else "filename". Mixed clocks cannot be sorted together,
    so one missing EXIF time falls the whole shoot back to filename order.
    """
    if features and all(_clock(f) == "exif" for f in features):
        ordered = sorted(features, key=lambda f: (f["captured_at_ms"], f["photo_id"]))
        return ordered, "capture_time"
    return sorted(features, key=lambda f: f["photo_id"]), "filename"


def _visual_distance(pair: dict) -> float:
    """Combined distance used by both post-passes. No fingerprint counts as 0."""
    if pair["dh"] is None or pair["dc"] is None:
        return 0.0
    return pair["dh"] + 64 * pair["dc"]


def _runs(boundaries: list[bool]) -> list[tuple[int, int]]:
    """Groups as (start, end) photo indices, end exclusive."""
    runs, start = [], 0
    for i, is_boundary in enumerate(boundaries):
        if is_boundary:
            runs.append((start, i + 1))
            start = i + 1
    runs.append((start, len(boundaries) + 1))
    return runs


def _is_hard_gap(pair: dict, th: dict) -> bool:
    return pair["dt"] is not None and pair["dt"] >= th["T_HARD"]


def _merge_small_groups(pairs: list[dict], boundaries: list[bool], th: dict) -> None:
    """
    MIN_GROUP pass. A group smaller than MIN_GROUP joins the neighbour with the
    smaller visual distance (ties go to the previous group). It stays on its
    own only when every side that has a neighbour is a hard time gap.
    """
    changed = True
    while changed:
        changed = False
        for start, end in _runs(boundaries):
            if end - start >= th["MIN_GROUP"]:
                continue
            sides = []                      # pair indices bordering this group
            if start > 0:
                sides.append(start - 1)
            if end - 1 < len(pairs):
                sides.append(end - 1)
            if not sides or all(_is_hard_gap(pairs[i], th) for i in sides):
                continue
            target = min(sides, key=lambda i: (_visual_distance(pairs[i]), i))
            boundaries[target] = False
            pairs[target]["merged"] = True
            changed = True
            break


def _split_large_groups(pairs: list[dict], boundaries: list[bool], th: dict) -> None:
    """
    MAX_GROUP pass. Cut an oversized group at its largest internal visual
    distance, never leaving a piece smaller than MIN_GROUP. Ties go to the cut
    nearest the middle, then the earlier one. Repeats until every group fits.
    """
    changed = True
    while changed:
        changed = False
        for start, end in _runs(boundaries):
            if end - start <= th["MAX_GROUP"]:
                continue
            # Pair i sits between photo i and i + 1. Cutting at i leaves
            # (i + 1 - start) photos before and (end - i - 1) after.
            candidates = range(start + th["MIN_GROUP"] - 1, end - th["MIN_GROUP"])
            middle = (start + end - 2) / 2
            cut = max(
                candidates,
                key=lambda i: (_visual_distance(pairs[i]), -abs(i - middle), -i),
            )
            boundaries[cut] = True
            pairs[cut]["forced"] = True
            changed = True
            break


def _boundary_info(pair: dict) -> dict:
    return {
        "reason": pair["final_reason"],
        "dt_s":   None if pair["dt"] is None else round(pair["dt"], 3),
        "dh":     pair["dh"],
        "dc":     None if pair["dc"] is None else round(pair["dc"], 4),
    }


def measure_pairs(ordered: list[dict]) -> list[dict]:
    """Distances for each adjacent pair of already-ordered photos."""
    measured = []
    for a, b in pairwise(ordered):
        dt, dh, dc = pair_distances(a, b)
        measured.append({"a": a["photo_id"], "b": b["photo_id"], "dt": dt, "dh": dh, "dc": dc})
    return measured


def group_photos(features: list[dict], thresholds: dict | None = None) -> dict:
    """
    Group a shoot into contiguous sets.

    Args:
        features:   records with photo_id, captured_at_ms, time_source, dhash,
                    hist (see module docstring). Any order.
        thresholds: overrides for THRESHOLDS.

    Returns the §7.5 shape, plus:
        order  "capture_time" | "filename"
        pairs  one record per adjacent pair, for evaluation and the golden
               fixture: distances, the table's decision, and whether the
               post-pass changed it.
    """
    ordered, order = order_photos(features)
    return group_measured(
        [f["photo_id"] for f in ordered], measure_pairs(ordered), thresholds, order
    )


def group_measured(
    photo_ids: list[str],
    measured: list[dict],
    thresholds: dict | None = None,
    order: str = "capture_time",
) -> dict:
    """
    Group photos whose pair distances are already known.

    This is the decision table plus post-pass with no feature work, so a
    threshold sweep can re-group one shoot thousands of times cheaply.

    Args:
        photo_ids: photos in shooting order.
        measured:  measure_pairs() output, one record per adjacent pair.
                   Not modified.
    """
    th = resolve_thresholds(thresholds)

    pairs: list[dict] = []
    for m in measured:
        boundary, reason, rule = decide_pair(m["dt"], m["dh"], m["dc"], th)
        pairs.append({
            "a": m["a"], "b": m["b"], "dt": m["dt"], "dh": m["dh"], "dc": m["dc"],
            "boundary": boundary, "reason": reason, "rule": rule,
        })

    boundaries = [p["boundary"] for p in pairs]
    _merge_small_groups(pairs, boundaries, th)
    _split_large_groups(pairs, boundaries, th)

    for pair, is_boundary in zip(pairs, boundaries):
        pair["final_boundary"] = is_boundary
        if not is_boundary:
            pair["final_reason"] = None
        elif pair.pop("forced", False):
            pair["final_reason"] = "max_size"
        else:
            pair["final_reason"] = pair["reason"]
        pair["merged"] = pair.pop("merged", False) and not is_boundary

    runs   = _runs(boundaries) if photo_ids else []
    width  = max(2, len(str(len(runs))))
    groups = []
    for n, (start, end) in enumerate(runs, 1):
        groups.append({
            "group_id":        f"g{n:0{width}d}",
            "photo_ids":       photo_ids[start:end],
            "source":          "auto",
            "boundary_before": {"reason": "start"} if start == 0
                               else _boundary_info(pairs[start - 1]),
        })

    return {
        "grouping_version": GROUPING_VERSION,
        "thresholds":       th,
        "order":            order,
        "groups":           groups,
        "pairs":            pairs,
    }


def public_grouping(grouping: dict) -> dict:
    """The §7.5 output shape: group_photos() without the per-pair diagnostics."""
    return {k: v for k, v in grouping.items() if k != "pairs"}
