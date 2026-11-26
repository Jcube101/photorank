"""Boundary detection: decision table, post-pass, ordering, features, golden fixture."""

import json
from pathlib import Path

import numpy as np
import pytest

from core import group
from core.group import (
    ADAPTIVE_DEFAULTS,
    GROUPING_VERSION,
    THRESHOLDS,
    adaptive_pause,
    decide_pair,
    dhash_from_bgr,
    group_measured,
    group_photos,
    hamming,
    hist_distance,
    hist_from_bgr,
    order_photos,
    pair_distances,
    resolve_thresholds,
)
from tests.fixtures import make_grouping_golden

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "grouping_golden.json").read_text()
)


# ---------------------------------------------------------------------------
# §7.3 decision table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("dt", "dh", "dc", "boundary", "reason", "rule"),
    [
        # Rule 1: long pause, not near-identical.
        (90,    7,  0.00, True,  "time_gap",      "1"),   # dh just over H_SAME
        (90,    0,  0.09, True,  "time_gap",      "1"),   # dc just over C_SAME
        (3600,  40, 0.90, True,  "time_gap",      "1"),
        # Rule 2: long pause, near-identical on BOTH -> same set resumed.
        (90,    6,  0.08, False, None,            "2"),   # both exactly at SAME
        (3600,  0,  0.00, False, None,            "2"),
        # Rule 3: medium pause, some change on EITHER signal.
        (15,    10, 0.00, True,  "time+visual",   "3"),   # dt == T_SOFT, dh == H_LOW
        (89.9,  0,  0.15, True,  "time+visual",   "3"),   # dc == C_LOW
        (50,    30, 0.50, True,  "time+visual",   "3"),
        # Rule 5 in the medium band: both below LOW.
        (50,    9,  0.14, False, None,            "5"),
        # Rule 4: quick follow-up, strong change on BOTH.
        (14.9,  24, 0.35, True,  "visual_change", "4"),   # both exactly at HIGH
        (0,     64, 1.00, True,  "visual_change", "4"),
        # Rule 5 in the fast band: one strong signal is not enough.
        (5,     24, 0.34, False, None,            "5"),
        (5,     23, 0.35, False, None,            "5"),
        (0,     0,  0.00, False, None,            "5"),
    ],
)
def test_decision_table(dt, dh, dc, boundary, reason, rule):
    assert decide_pair(dt, dh, dc) == (boundary, reason, rule)


@pytest.mark.parametrize(
    ("fast_rule", "dh", "dc", "boundary"),
    [
        ("and", 30, 0.10, False), ("and", 10, 0.50, False), ("and", 30, 0.50, True),
        ("or",  30, 0.10, True),  ("or",  10, 0.50, True),  ("or",  10, 0.10, False),
        ("dh",  30, 0.10, True),  ("dh",  10, 0.50, False), ("dh",  30, 0.50, True),
    ],
)
def test_fast_rule_combinator(fast_rule, dh, dc, boundary):
    th = resolve_thresholds({"FAST_RULE": fast_rule})
    assert decide_pair(5, dh, dc, th)[0] is boundary


def test_fast_rule_only_affects_quick_follow_ups():
    th = resolve_thresholds({"FAST_RULE": "or"})
    assert decide_pair(50, 9, 0.14, th) == decide_pair(50, 9, 0.14)
    assert decide_pair(200, 3, 0.02, th) == decide_pair(200, 3, 0.02)


@pytest.mark.parametrize(
    ("dt", "dh", "dc", "expected"),
    [
        # No time: visual_only needs BOTH at LOW or above.
        (None, 10, 0.15, (True,  "visual_only", "visual_only")),
        (None, 9,  0.90, (False, None,          "visual_only")),
        (None, 60, 0.14, (False, None,          "visual_only")),
        # No fingerprint: time alone, T_HARD only.
        (90,   None, None, (True,  "time_gap", "time_only")),
        (89,   None, None, (False, None,       "time_only")),
        # A half fingerprint is no fingerprint.
        (200,  5,    None, (True,  "time_gap", "time_only")),
        (200,  None, 0.5,  (True,  "time_gap", "time_only")),
        # Nothing to go on: never split.
        (None, None, None, (False, None, "no_data")),
    ],
)
def test_missing_data(dt, dh, dc, expected):
    assert decide_pair(dt, dh, dc) == expected


def test_thresholds_are_validated():
    with pytest.raises(ValueError, match="Unknown"):
        resolve_thresholds({"T_MEDIUM": 5})
    with pytest.raises(ValueError, match="FAST_RULE"):
        resolve_thresholds({"FAST_RULE": "xor"})
    with pytest.raises(ValueError, match="T_SOFT"):
        resolve_thresholds({"T_SOFT": 100})
    with pytest.raises(ValueError, match="MAX_GROUP"):
        resolve_thresholds({"MIN_GROUP": 3, "MAX_GROUP": 5})
    assert resolve_thresholds() == THRESHOLDS
    assert resolve_thresholds() is not THRESHOLDS


# ---------------------------------------------------------------------------
# Distances and ordering
# ---------------------------------------------------------------------------

def _photo(pid, ms=None, source="none", dhash="0" * 16, hist=None):
    return {"photo_id": pid, "captured_at_ms": ms, "time_source": source,
            "dhash": dhash, "hist": hist or bytes([200, 0] + [0] * 126).hex()}


def test_hamming_and_hist_distance():
    assert hamming("0000000000000000", "ffffffffffffffff") == 64
    assert hamming("00000000000000f0", "000000000000000f") == 8
    a = bytes([100, 100] + [0] * 126).hex()
    b = bytes([200, 0] + [0] * 126).hex()
    c = bytes([0, 0, 50] + [0] * 125).hex()
    assert hist_distance(a, a) == 0.0
    assert hist_distance(a, b) == pytest.approx(0.5)
    assert hist_distance(a, c) == 1.0
    # Scale-invariant: the histogram is normalised before comparing.
    assert hist_distance(bytes([50, 50] + [0] * 126).hex(), b) == pytest.approx(0.5)
    assert hist_distance("00" * 128, a) == 1.0


def test_dt_needs_a_shared_clock():
    exif  = _photo("a", 1_000, "exif")
    sub   = _photo("b", 3_500, "exif_subsec")
    mtime = _photo("c", 9_000, "file_mtime")
    none  = _photo("d")
    assert pair_distances(exif, sub)[0] == 2.5
    assert pair_distances(sub, exif)[0] == 2.5           # never negative
    assert pair_distances(exif, mtime)[0] is None
    assert pair_distances(mtime, _photo("e", 10_000, "file_mtime"))[0] == 1.0
    assert pair_distances(exif, none)[0] is None


def test_order_uses_time_only_when_every_photo_has_exif():
    a, b, c = _photo("C.JPG", 1, "exif"), _photo("A.JPG", 2, "exif"), _photo("B.JPG", 2, "exif")
    ordered, order = order_photos([b, c, a])
    assert order == "capture_time"
    assert [p["photo_id"] for p in ordered] == ["C.JPG", "A.JPG", "B.JPG"]

    ordered, order = order_photos([b, c, a, _photo("0.JPG")])
    assert order == "filename"
    assert [p["photo_id"] for p in ordered] == ["0.JPG", "A.JPG", "B.JPG", "C.JPG"]


def test_empty_and_single_photo():
    assert group_photos([])["groups"] == []
    result = group_photos([_photo("only.JPG", 5, "exif")])
    assert [g["photo_ids"] for g in result["groups"]] == [["only.JPG"]]
    assert result["groups"][0]["boundary_before"] == {"reason": "start"}


def test_groups_cover_every_photo_once_and_respect_size_limits():
    rng = np.random.default_rng(7)
    photos, ms = [], 0
    for i in range(300):
        ms += int(rng.choice([500, 2_000, 30_000, 200_000]))
        photos.append({
            "photo_id": f"DSC{i:05d}.JPG", "captured_at_ms": ms, "time_source": "exif",
            "dhash": f"{int(rng.integers(0, 2**63)):016x}",
            "hist":  bytes(int(x) for x in rng.integers(0, 256, 128)).hex(),
        })
    result = group_photos(photos, {"T_HARD": 100_000, "T_SOFT": 100_000, "MAX_GROUP": 8})
    flat = [pid for g in result["groups"] for pid in g["photo_ids"]]
    assert flat == [p["photo_id"] for p in photos]
    sizes = [len(g["photo_ids"]) for g in result["groups"]]
    assert max(sizes) <= 8 and min(sizes) >= 2      # no hard gaps -> no kept singletons
    assert [g["group_id"] for g in result["groups"]] == sorted(g["group_id"] for g in result["groups"])


# ---------------------------------------------------------------------------
# Adaptive time rule (optional, off by default)
# ---------------------------------------------------------------------------

ADAPTIVE = {"TIME_RULE": "adaptive", "T_MIN": 5, "ADAPT_K": 4, "ADAPT_N": 3}


def _measured(steps):
    """Pairs from (dt, dh, dc) steps, with photo ids p00, p01, ..."""
    return [{"a": f"p{i:02d}", "b": f"p{i + 1:02d}", "dt": dt, "dh": dh, "dc": dc}
            for i, (dt, dh, dc) in enumerate(steps)]


def _ids(n):
    return [f"p{i:02d}" for i in range(n)]


def test_default_output_has_no_adaptive_keys():
    result = group_measured(_ids(3), _measured([(2, 3, 0.03), (2, 3, 0.03)]))
    assert result["thresholds"] == THRESHOLDS
    assert all("t_pause" not in p for p in result["pairs"])
    with_fixed = resolve_thresholds({"TIME_RULE": "fixed"})
    assert with_fixed == {**THRESHOLDS, **ADAPTIVE_DEFAULTS}


def test_adaptive_thresholds_are_validated():
    with pytest.raises(ValueError, match="TIME_RULE"):
        resolve_thresholds({"TIME_RULE": "auto"})
    with pytest.raises(ValueError, match="ADAPT_N"):
        resolve_thresholds({"TIME_RULE": "adaptive", "ADAPT_N": 0})
    with pytest.raises(ValueError, match="ADAPT_K"):
        resolve_thresholds({"TIME_RULE": "adaptive", "ADAPT_K": 0})
    assert resolve_thresholds({"ADAPT_K": 6})["TIME_RULE"] == "fixed"


def test_adaptive_pause_follows_the_recent_same_set_median():
    th = resolve_thresholds(ADAPTIVE)
    assert adaptive_pause([], th) == THRESHOLDS["T_SOFT"]         # no history yet
    assert adaptive_pause([1, 1, 1], th) == 5                       # floor T_MIN
    assert adaptive_pause([2, 3, 4], th) == 12                      # 4 x median 3
    assert adaptive_pause([2, 4], th) == 12                         # even count: mean of the middle two
    assert adaptive_pause([30, 30, 2, 3, 4], th) == 12              # only the last ADAPT_N count


def test_adaptive_rule_splits_a_short_pause_with_a_visual_change():
    # A steady 2 s pace, then a 10 s pause with a framing change (dh 20).
    steps = [(2, 3, 0.03)] * 4 + [(10, 20, 0.05)] + [(2, 3, 0.03)] * 2
    ids, measured = _ids(len(steps) + 1), _measured(steps)

    fixed = group_measured(ids, measured)
    assert len(fixed["groups"]) == 1                   # 10 s < T_SOFT 20: rule 4 needs dh and dc

    adaptive = group_measured(ids, measured, ADAPTIVE)
    assert [len(g["photo_ids"]) for g in adaptive["groups"]] == [5, 3]
    pause = adaptive["pairs"][4]
    assert (pause["t_pause"], pause["rule"], pause["reason"]) == (8, "3", "time+visual")
    assert adaptive["groups"][1]["boundary_before"]["pause_s"] == 8


def test_adaptive_rule_needs_the_visual_change_too():
    steps = [(2, 3, 0.03)] * 4 + [(10, 3, 0.03)] + [(2, 3, 0.03)] * 2
    result = group_measured(_ids(len(steps) + 1), _measured(steps), ADAPTIVE)
    assert len(result["groups"]) == 1
    assert result["pairs"][4]["rule"] == "5"


def test_adaptive_history_counts_only_same_set_pairs():
    # Two 60 s boundaries must not raise the pace estimate for the pair after them.
    steps = [(2, 3, 0.03), (2, 3, 0.03), (60, 30, 0.4), (60, 30, 0.4), (2, 3, 0.03),
             (9, 20, 0.05), (2, 3, 0.03)]
    result = group_measured(_ids(len(steps) + 1), _measured(steps), {**ADAPTIVE, "MIN_GROUP": 1})
    assert [p["t_pause"] for p in result["pairs"]] == [THRESHOLDS["T_SOFT"], 8, 8, 8, 8, 8, 8]
    assert [p["boundary"] for p in result["pairs"]] == [False, False, True, True, False, True, False]


def test_adaptive_rule_leaves_missing_time_alone():
    steps = [(2, 3, 0.03), (None, 20, 0.05), (2, 3, 0.03)]
    result = group_measured(_ids(4), _measured(steps), ADAPTIVE)
    assert result["pairs"][1]["rule"] == "visual_only"
    assert result["pairs"][2]["t_pause"] == 8          # the None pair added nothing to the history


# ---------------------------------------------------------------------------
# Feature extraction on synthetic images
# ---------------------------------------------------------------------------

def _solid(b, g, r):
    return np.full((64, 64, 3), (b, g, r), dtype=np.uint8)


def test_dhash_is_left_greater_than_right_msb_first():
    ramp = np.zeros((64, 64, 3), dtype=np.uint8)
    ramp[:, :, :] = np.linspace(255, 0, 64, dtype=np.uint8)[None, :, None]   # bright -> dark
    assert dhash_from_bgr(ramp) == "f" * 16
    assert dhash_from_bgr(ramp[:, ::-1]) == "0" * 16
    assert dhash_from_bgr(_solid(90, 90, 90)) == "0" * 16

    # Only the top row has a gradient: the first byte is set, the rest are not.
    top = np.zeros((64, 64, 3), dtype=np.uint8)
    top[:8] = ramp[:8]
    assert dhash_from_bgr(top) == "ff" + "0" * 14


def test_hist_bins_are_hue_sat_value():
    def only_bin(img):
        raw = bytes.fromhex(hist_from_bgr(img))
        assert len(raw) == 128
        hot = [i for i, v in enumerate(raw) if v]
        assert len(hot) == 1 and raw[hot[0]] == 255
        return hot[0]

    assert only_bin(_solid(0, 0, 255)) == 0 * 16 + 3 * 4 + 3      # pure red: hue 0
    assert only_bin(_solid(0, 255, 0)) == 2 * 16 + 3 * 4 + 3      # pure green: hue 120
    assert only_bin(_solid(255, 0, 0)) == 5 * 16 + 3 * 4 + 3      # pure blue: hue 240
    assert only_bin(_solid(0, 0, 0)) == 0                         # black
    assert only_bin(_solid(255, 255, 255)) == 3                   # white: s=0, v=3
    assert only_bin(_solid(100, 100, 100)) == 1                   # grey: s=0, v=1
    assert only_bin(_solid(255, 0, 255)) == 6 * 16 + 3 * 4 + 3    # magenta: hue 300


def test_hist_quantisation_makes_the_fullest_bin_255():
    img = _solid(0, 0, 255)
    img[:16] = (255, 0, 0)                     # a quarter blue
    raw = bytes.fromhex(hist_from_bgr(img))
    assert raw[0 * 16 + 15] == 255
    assert raw[5 * 16 + 15] == 85              # 1/3 of the fullest bin
    assert hist_distance(hist_from_bgr(img), hist_from_bgr(_solid(0, 0, 255))) == pytest.approx(0.25)


def test_fingerprint_and_capture_time_from_files(tmp_path):
    import cv2
    from PIL import Image

    path = tmp_path / "a.jpg"
    cv2.imwrite(str(path), _solid(0, 0, 255))
    dhash, hist = group.compute_fingerprint(path)
    assert len(dhash) == 16 and len(hist) == 256
    assert group.compute_fingerprint(tmp_path / "missing.jpg") == (None, None)
    assert group.read_capture_time(path) == (None, "none")
    assert group.read_capture_time(tmp_path / "missing.jpg") == (None, "none")

    def with_exif(name, subsec):
        exif = Image.Exif()
        ifd = exif.get_ifd(0x8769)
        ifd[0x9003] = "2026:10:03 17:02:11"
        if subsec is not None:
            ifd[0x9291] = subsec
        out = tmp_path / name
        Image.new("RGB", (8, 8)).save(out, exif=exif)
        return out

    base, source = group.read_capture_time(with_exif("b.jpg", None))
    assert source == "exif" and base % 1000 == 0
    assert group.read_capture_time(with_exif("c.jpg", "34")) == (base + 340, "exif_subsec")
    assert group.read_capture_time(with_exif("d.jpg", "5")) == (base + 500, "exif_subsec")
    assert group.read_capture_time(with_exif("e.jpg", "1234")) == (base + 123, "exif_subsec")


# ---------------------------------------------------------------------------
# Golden fixture (shared contract with the JS port)
# ---------------------------------------------------------------------------

def test_fixture_pins_version_and_thresholds():
    assert FIXTURE["grouping_version"] == GROUPING_VERSION
    assert FIXTURE["thresholds"] == THRESHOLDS


def test_fixture_is_current():
    """The committed JSON must be what the generator (with its hand-written expectations) builds."""
    assert json.loads(json.dumps(make_grouping_golden.build())) == FIXTURE


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_golden_case(case):
    result   = group_photos(case["photos"], case["thresholds"])
    expected = case["expected"]

    assert result["order"] == expected["order"]
    assert [g["photo_ids"] for g in result["groups"]] == [g["photo_ids"] for g in expected["groups"]]
    assert [g["group_id"] for g in result["groups"]] == [g["group_id"] for g in expected["groups"]]
    assert [g["boundary_before"] for g in result["groups"]] == [
        g["boundary_before"] for g in expected["groups"]
    ]

    assert len(result["pairs"]) == len(expected["pairs"])
    for got, want in zip(result["pairs"], expected["pairs"], strict=True):
        assert (got["a"], got["b"]) == (want["a"], want["b"])
        assert got["dt"] == want["dt_s"]
        assert got["dh"] == want["dh"]
        assert got["dc"] == pytest.approx(want["dc"], abs=1e-9)
        for key in ("boundary", "reason", "rule", "final_boundary", "final_reason"):
            assert got[key] == want[key], key


def test_fixture_covers_every_rule_and_reason():
    rules   = {p["rule"] for c in FIXTURE["cases"] for p in c["expected"]["pairs"]}
    reasons = {g["boundary_before"]["reason"] for c in FIXTURE["cases"] for g in c["expected"]["groups"]}
    assert rules == {"1", "2", "3", "4", "5", "visual_only", "time_only", "no_data"}
    assert reasons == {"start", "time_gap", "time+visual", "visual_change", "visual_only", "max_size"}
