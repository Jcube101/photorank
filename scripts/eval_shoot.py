"""
Evaluate full shoot mode against a hand-labelled answer key (docs/V2-PLAN.md §12).

    PYTHONPATH=. ./venv/bin/python scripts/eval_shoot.py \\
        --input input/wat_arun --key eval/wat_arun/answer_key.json \\
        --stage grouping|oracle|e2e|all \\
        --cache output/eval_wat_arun_scores.json --runs 3

Stages
  grouping  Boundary metrics for the default thresholds, the dt/dh/dc
            distributions for true-boundary vs non-boundary pairs, and a
            threshold sweep. No Gemini calls.
  oracle    Score each TRUE set from the key and measure the picks: top-1,
            top-3, MRR, ablations, spread on vs off, a weight sweep, and
            diagnostics. Calls Gemini once per set per run per spread variant.
  e2e       Predicted groups -> predicted winners, compared with the key.

Cache
  Every raw score is saved to --cache as soon as its set is scored, so weight
  and prompt comparisons re-run without calling Gemini, and an interrupted
  run resumes. The cache holds numbers, Gemini notes and filenames only,
  never pixels. It lives on the developer's machine (output/ is git-ignored).
  Delete the file to force re-scoring.

The answer key format is in eval/README.md.
"""

import argparse
import hashlib
import itertools
import json
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from core.group import (
    ADAPTIVE_DEFAULTS,
    THRESHOLDS,
    extract_features,
    group_measured,
    measure_pairs,
    order_photos,
)
from core.profiles import SHOOT_AXES, SHOOT_AXIS_SOURCES, SHOOT_WEIGHTS
from core.shoot import rank_group

CACHE_VERSION = 1
V1_BLUR_GATE  = 100.0

# Threshold values tried by the grouping sweep. 65 and 1.01 are out of range
# for dh and dc, so they switch that signal off in rule 3.
SWEEP_GRID: dict[str, list] = {
    "T_SOFT":    [10, 15, 20, 30],
    "T_HARD":    [60, 90, 120, 180],
    "H_SAME":    [4, 6, 8],
    "C_SAME":    [0.05, 0.08, 0.12],
    "H_LOW":     [10, 14, 18, 65],
    "C_LOW":     [0.15, 0.20, 0.30, 1.01],
    "H_HIGH":    [18, 24, 30],
    "C_HIGH":    [0.25, 0.35, 0.45],
    "FAST_RULE": ["and", "or", "dh"],
    "MIN_GROUP": [1, 2],
}

# Fixed vs adaptive time rule (V2-PLAN §7.3, §12.2 step 4). Both rules are
# swept over the same grid of the thresholds that interact with time; the
# others stay at their defaults. The fixed rule varies T_SOFT. The adaptive
# rule replaces T_SOFT with three knobs (core/group.py:ADAPTIVE_DEFAULTS).
TIME_RULE_SHARED: dict[str, list] = {
    "T_HARD":    [60, 90, 120],
    "H_LOW":     [10, 14, 18, 65],
    "C_LOW":     [0.15, 0.20, 0.30, 1.01],
    "FAST_RULE": ["and", "or", "dh"],
    "MIN_GROUP": [1, 2],
}
FIXED_TIME_GRID: dict[str, list] = {"T_SOFT": [5, 8, 10, 12, 15, 20, 30]}
ADAPTIVE_TIME_GRID: dict[str, list] = {
    "TIME_RULE": ["adaptive"],
    "T_MIN":     [4, 6, 8, 10, 12],
    "ADAPT_K":   [2, 3, 4, 6, 8],
    "ADAPT_N":   [5, 10, 20],
}


def _stem(photo_id: str) -> str:
    return Path(photo_id).stem


# ---------------------------------------------------------------------------
# Answer key
# ---------------------------------------------------------------------------

def load_key(path: str | Path) -> dict:
    key = json.loads(Path(path).read_text())
    if not isinstance(key.get("sets"), list) or not key["sets"]:
        raise ValueError("Answer key needs a non-empty 'sets' list")
    return key


def resolve_key(key: dict, ordered_ids: list[str]) -> list[dict]:
    """
    Check the key against the photos and expand it.

    Sets must cover every photo, contiguously, in shooting order, with no
    overlaps, and each needs exactly one pick inside the set.

    Returns [{"set", "photo_ids", "pick", "acceptable"}] with full photo ids.
    Raises ValueError listing every problem found.
    """
    index: dict[str, int] = {}
    for i, pid in enumerate(ordered_ids):
        if _stem(pid) in index:
            raise ValueError(f"Two photos share the name {_stem(pid)!r}; the key cannot tell them apart")
        index[_stem(pid)] = i

    problems: list[str] = []
    resolved: list[dict] = []
    expected_start = 0
    for entry in key["sets"]:
        label = f"set {entry.get('set', '?')}"
        missing = [f for f in ("first", "last", "pick") if f not in entry]
        if missing:
            problems.append(f"{label}: missing {missing}")
            continue
        unknown = [entry[f] for f in ("first", "last", "pick") if entry[f] not in index]
        unknown += [a for a in entry.get("acceptable", []) if a not in index]
        if unknown:
            problems.append(f"{label}: not in the input folder: {unknown}")
            continue

        first, last = index[entry["first"]], index[entry["last"]]
        if last < first:
            problems.append(f"{label}: 'last' comes before 'first' in shooting order")
            continue
        if first != expected_start:
            what = "overlaps the previous set" if first < expected_start else (
                f"leaves {first - expected_start} photo(s) uncovered before it "
                f"(from {_stem(ordered_ids[expected_start])})"
            )
            problems.append(f"{label}: {what}")
        expected_start = last + 1

        names   = [entry["pick"], *entry.get("acceptable", [])]
        outside = [name for name in names if not first <= index[name] <= last]
        if entry["pick"] in outside:
            problems.append(f"{label}: pick {entry['pick']} is outside the set")
            outside.remove(entry["pick"])
        if outside:
            problems.append(f"{label}: acceptable {outside} outside the set")

        resolved.append({
            "set":        entry.get("set"),
            "photo_ids":  ordered_ids[first : last + 1],
            "pick":       ordered_ids[index[entry["pick"]]],
            "acceptable": [ordered_ids[index[a]] for a in entry.get("acceptable", [])],
        })

    if not problems and expected_start != len(ordered_ids):
        problems.append(
            f"{len(ordered_ids) - expected_start} photo(s) after the last set are uncovered "
            f"(from {_stem(ordered_ids[expected_start])})"
        )
    declared = key.get("photo_count")
    if declared is not None and declared != len(ordered_ids):
        problems.append(f"photo_count is {declared} but the input has {len(ordered_ids)} photos")
    if problems:
        raise ValueError("Answer key problems:\n  " + "\n  ".join(problems))
    return resolved


# ---------------------------------------------------------------------------
# Boundary metrics (§12.2)
# ---------------------------------------------------------------------------

def boundaries_of(groups: list[list[str]]) -> set[int]:
    """Pair indices that are boundaries. Pair i sits between photo i and i + 1."""
    out, position = set(), 0
    for ids in groups[:-1]:
        position += len(ids)
        out.add(position - 1)
    return out


def _f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall    = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def boundary_metrics(predicted: list[list[str]], truth: list[list[str]]) -> dict:
    pred, true = boundaries_of(predicted), boundaries_of(truth)
    tp, fp, fn = len(pred & true), len(pred - true), len(true - pred)
    precision, recall, f1 = _f1(tp, fp, fn)

    # ±1 tolerant: each true boundary may absorb one predicted boundary at most
    # one frame away.
    unmatched, tolerant_tp = set(pred), 0
    for t in sorted(true):
        for candidate in (t, t - 1, t + 1):
            if candidate in unmatched:
                unmatched.discard(candidate)
                tolerant_tp += 1
                break
    _, _, tolerant_f1 = _f1(tolerant_tp, len(pred) - tolerant_tp, len(true) - tolerant_tp)

    predicted_sets = {tuple(ids) for ids in predicted}
    exact = sum(1 for ids in truth if tuple(ids) in predicted_sets)
    return {
        "true_boundaries":      len(true),
        "predicted_boundaries": len(pred),
        "precision":            precision,
        "recall":               recall,
        "f1":                   f1,
        "false_boundaries":     fp,      # each needs a merge
        "missed_boundaries":    fn,      # each needs a split
        "corrections":          fp + fn,
        "exact_set_match":      exact / len(truth),
        "tolerant_f1":          tolerant_f1,
    }


def _quantiles(values: list[float]) -> str:
    if not values:
        return "n/a"
    v = sorted(values)
    return " / ".join(
        f"{v[min(len(v) - 1, round(q * (len(v) - 1)))]:.3g}" for q in (0, 0.25, 0.5, 0.75, 1)
    )


def sweep_thresholds(
    ordered_ids: list[str],
    measured: list[dict],
    truth: list[list[str]],
    order: str,
    grid: dict[str, list] | None = None,
) -> dict:
    """Grid-sweep the §7.3 thresholds. Returns the best score and how stable each value is."""
    grid  = SWEEP_GRID if grid is None else grid
    names = list(grid)
    true  = boundaries_of(truth)

    results: list[tuple[int, tuple]] = []
    for combo in itertools.product(*(grid[n] for n in names)):
        grouping = group_measured(ordered_ids, measured, dict(zip(names, combo, strict=True)), order)
        pred = {i for i, p in enumerate(grouping["pairs"]) if p["final_boundary"]}
        results.append((len(pred ^ true), combo))

    best = min(c for c, _ in results)
    near = [combo for c, combo in results if c <= best + 1]

    # Of the combinations that reach the best, the one that changes the
    # fewest defaults (grid order breaks ties): the cheapest change to adopt.
    defaults = {**THRESHOLDS, **ADAPTIVE_DEFAULTS}
    def changes(combo: tuple) -> int:
        return sum(1 for n, v in zip(names, combo, strict=True)
                   if n != "TIME_RULE" and defaults[n] != v)
    closest = min((combo for c, combo in results if c == best), key=changes)

    marginals = {}
    for i, name in enumerate(names):
        marginals[name] = []
        for value in grid[name]:
            scores = [c for c, combo in results if combo[i] == value]
            marginals[name].append({
                "value":      value,
                "best":       min(scores),
                "near_share": sum(1 for c in scores if c <= best + 1) / len(scores),
            })
    return {
        "combos":           len(results),
        "best_corrections": best,
        "at_best":          sum(1 for c, _ in results if c == best),
        "near_best":        len(near),
        "marginals":        marginals,
        "best_examples":    [dict(zip(names, combo, strict=True)) for c, combo in results if c == best][:5],
        "closest_best":     dict(zip(names, closest, strict=True)),
        "closest_changes":  changes(closest),
    }


def compare_time_rules(
    ordered_ids: list[str],
    measured: list[dict],
    truth: list[list[str]],
    order: str,
) -> dict:
    """
    Best fixed vs best adaptive time rule over the same grid (§7.3).

    For each rule: the sweep, and the best combination closest to the
    defaults with its boundary metrics and grouping. "default" is today's
    thresholds, for reference.
    """
    out = {"default": {"grouping": group_measured(ordered_ids, measured, None, order)}}
    for name, extra in (("fixed", FIXED_TIME_GRID), ("adaptive", ADAPTIVE_TIME_GRID)):
        sweep = sweep_thresholds(ordered_ids, measured, truth, order, {**TIME_RULE_SHARED, **extra})
        out[name] = {
            "sweep":    sweep,
            "chosen":   sweep["closest_best"],
            "grouping": group_measured(ordered_ids, measured, sweep["closest_best"], order),
        }
    for result in out.values():
        predicted = [g["photo_ids"] for g in result["grouping"]["groups"]]
        result["metrics"] = boundary_metrics(predicted, truth)
    return out


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class Cache:
    """Features and raw scores on disk. Written atomically after every change."""

    def __init__(self, path: str | Path | None):
        self.path  = Path(path) if path else None
        self._lock = threading.Lock()
        self.data  = {"version": CACHE_VERSION, "features": {}, "scores": {}}
        if self.path and self.path.exists():
            loaded = json.loads(self.path.read_text())
            if loaded.get("version") != CACHE_VERSION:
                raise ValueError(
                    f"{self.path} is cache version {loaded.get('version')}, expected "
                    f"{CACHE_VERSION}. Delete it to re-score."
                )
            self.data = loaded

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)

    @staticmethod
    def group_key(photo_ids: list[str]) -> str:
        return hashlib.sha256("|".join(photo_ids).encode()).hexdigest()[:20]

    def get_scores(self, variant: str, run: int, photo_ids: list[str]) -> dict | None:
        return self.data["scores"].get(variant, {}).get(str(run), {}).get(self.group_key(photo_ids))

    def put_scores(self, variant: str, run: int, photo_ids: list[str], scored: dict) -> None:
        with self._lock:
            runs = self.data["scores"].setdefault(variant, {}).setdefault(str(run), {})
            runs[self.group_key(photo_ids)] = scored
        self.save()

    def slice_scores(self, variant: str, run: int, photo_ids: list[str]) -> dict | None:
        """
        Scores for a set that was split after it was scored: the rows of a
        larger cached group, from the same run, that holds the set as one
        contiguous run of photos. This is the plan's on-device split (§10.4):
        both halves come from one Gemini call, so their scores stay
        comparable. Returns None when no cached group holds the set.
        """
        want = list(photo_ids)
        for group in self.data["scores"].get(variant, {}).get(str(run), {}).values():
            ids = [p["photo_id"] for p in group["photos"]]
            if len(ids) <= len(want):
                continue
            for start in range(len(ids) - len(want) + 1):
                if ids[start : start + len(want)] == want:
                    return {
                        "photos":      [dict(p) for p in group["photos"][start : start + len(want)]],
                        "photo_count": len(want),
                        "sliced_from": len(ids),
                    }
        return None


def variant_key(force_spread: bool) -> str:
    """Scores are only comparable within one model, thinking level and prompt variant."""
    from core.score_vision import GEMINI_MODEL, GEMINI_THINKING_LEVEL
    spread = "spread_on" if force_spread else "spread_off"
    return f"{GEMINI_MODEL}|thinking={GEMINI_THINKING_LEVEL or 'default'}|{spread}"


def load_features(input_dir: str | Path, cache: Cache) -> list[dict]:
    """Feature records for every photo, re-using cached ones whose file is unchanged."""
    from core.ingest import cleanup, collect_images, ingest

    paths = collect_images(input_dir)
    if not paths:
        raise ValueError(f"No supported images found in: {input_dir}")

    def signature(p: Path) -> list[int]:
        stat = p.stat()
        return [stat.st_size, stat.st_mtime_ns]

    cached  = cache.data["features"]
    pending = [p for p in paths if cached.get(p.name, {}).get("sig") != signature(p)]
    if pending:
        print(f"[eval] fingerprinting {len(pending)} photo(s)...", file=sys.stderr)
        photos, temp_dir = ingest(pending)
        try:
            for feature in extract_features(photos):
                source = next(p for p in pending if p.name == feature["photo_id"])
                cached[feature["photo_id"]] = {**feature, "sig": signature(source)}
        finally:
            cleanup(temp_dir)
        cache.save()

    unreadable = [p.name for p in paths if p.name not in cached]
    if unreadable:
        raise ValueError(f"Could not read {len(unreadable)} photo(s): {unreadable[:5]}")
    return [{k: v for k, v in cached[p.name].items() if k != "sig"} for p in paths]


def score_sets(
    input_dir: str | Path,
    sets: list[list[str]],
    cache: Cache,
    force_spread: bool,
    run: int,
    cached_only: bool = False,
) -> list[dict | None]:
    """
    Raw scores for each set, from the cache where possible.

    Returns one entry per set, in order. An entry is None when scoring failed;
    failures are reported and never filled with default scores.

    With cached_only, Gemini is never called. A set that is not cached as
    itself is sliced from a larger cached group that holds it
    (Cache.slice_scores); otherwise its entry is None.
    """
    variant = variant_key(force_spread)
    results = [cache.get_scores(variant, run, ids) for ids in sets]
    pending = [i for i, r in enumerate(results) if r is None]
    if not pending:
        return results
    if cached_only:
        for i in pending:
            results[i] = cache.slice_scores(variant, run, sets[i])
        return results

    from core.ingest import cleanup, ingest
    from core.score_vision import MAX_CONCURRENCY
    from core.shoot import score_group

    spread = "on" if force_spread else "off"
    print(
        f"[eval] scoring {len(pending)} set(s) via Gemini (run {run + 1}, spread {spread}; "
        f"{len(sets) - len(pending)} cached)...",
        file=sys.stderr,
    )

    def _score(i: int) -> None:
        ids = sets[i]
        photos, temp_dir = ingest([Path(input_dir) / pid for pid in ids])
        try:
            if len(photos) != len(ids):
                raise ValueError(f"only {len(photos)} of {len(ids)} photos could be read")
            scored = score_group(photos, force_spread=force_spread)
        except Exception as e:  # noqa: BLE001 - one set failing must not stop the rest
            print(f"[eval] set starting {_stem(ids[0])} FAILED: {e}", file=sys.stderr)
            return
        finally:
            cleanup(temp_dir)
        scored["photo_count"] = len(ids)
        cache.put_scores(variant, run, ids, scored)
        results[i] = scored

    with ThreadPoolExecutor(max_workers=max(1, min(len(pending), MAX_CONCURRENCY))) as pool:
        list(pool.map(_score, pending))
    return results


# ---------------------------------------------------------------------------
# Pick metrics (§12.3)
# ---------------------------------------------------------------------------

def renormalised(axes: list[str]) -> dict[str, float]:
    """SHOOT_WEIGHTS restricted to some axes and rescaled to sum to 1."""
    total = sum(SHOOT_WEIGHTS[a] for a in axes)
    return {a: (SHOOT_WEIGHTS[a] / total if a in axes else 0.0) for a in SHOOT_AXES}


def _weights(**nonzero: float) -> dict[str, float]:
    return {a: nonzero.get(a, 0.0) for a in SHOOT_AXES}


# Simple, round SHOOT_WEIGHTS candidates for the oracle report (§12.3). A few
# sensible points instead of the sweep's single best: with ~30 sets one set is
# about 3 points, less than Gemini's run-to-run range, so prefer the simplest
# candidate that is within range of the best on both top-1 and top-3.
WEIGHT_CANDIDATES: list[dict[str, float]] = [
    dict(SHOOT_WEIGHTS),
    _weights(face_sharpness=0.1, expression=0.3, composition=0.3, subject_focus=0.3),
    _weights(face_sharpness=0.1, expression=0.3, camera_engagement=0.2, composition=0.2,
             subject_focus=0.2),
    _weights(face_sharpness=0.2, expression=0.3, camera_engagement=0.1, composition=0.2,
             subject_focus=0.2),
    _weights(face_sharpness=0.2, expression=0.2, camera_engagement=0.2, composition=0.2,
             subject_focus=0.2),
    _weights(expression=0.25, camera_engagement=0.25, composition=0.25, subject_focus=0.25),
    _weights(expression=0.4, composition=0.3, subject_focus=0.3),
    _weights(expression=1.0),
]


def relative_rank_metrics(truth: list[dict], scored: list[dict | None]) -> dict:
    """Top-1 / top-3 / MRR of Gemini's own relative_rank, with no weights at all."""
    ranks = []
    for entry, group in zip(truth, scored, strict=True):
        if group is None:
            continue
        order = [p["photo_id"] for p in sorted(group["photos"], key=lambda p: p["relative_rank"])]
        ranks.append(order.index(entry["pick"]) + 1)
    if not ranks:
        return {"sets": 0}
    n = len(ranks)
    return {"sets": n, "top1": sum(r == 1 for r in ranks) / n,
            "top3": sum(r <= 3 for r in ranks) / n, "mrr": sum(1 / r for r in ranks) / n}


def ablation_weights() -> dict[str, dict[str, float]]:
    gemini = [a for a in SHOOT_AXES if SHOOT_AXIS_SOURCES[a] == "gemini"]
    return {
        "face_sharpness only":           renormalised(["face_sharpness"]),
        "Gemini axes only":              renormalised(gemini),
        "SHOOT_WEIGHTS (combined)":      dict(SHOOT_WEIGHTS),
    }


def pick_metrics(
    truth: list[dict],
    scored: list[dict | None],
    weights: dict[str, float] | None = None,
) -> dict:
    """Top-1 / top-3 / MRR of the key's picks, over the sets that were scored."""
    ranks, lenient_hits, baseline = [], 0, []
    for entry, group in zip(truth, scored, strict=True):
        if group is None:
            continue
        order = [r["photo_id"] for r in rank_group(group["photos"], weights)]
        ranks.append(order.index(entry["pick"]) + 1)
        lenient_hits += order[0] == entry["pick"] or order[0] in entry["acceptable"]
        baseline.append(1 / len(order))
    n = len(ranks)
    if n == 0:
        return {"sets": 0}
    return {
        "sets":         n,
        "top1":         sum(1 for r in ranks if r == 1) / n,
        "top3":         sum(1 for r in ranks if r <= 3) / n,
        "mrr":          sum(1 / r for r in ranks) / n,
        "lenient_top1": lenient_hits / n,
        "random_top1":  sum(baseline) / n,
    }


def _mean_range(values: list[float]) -> str:
    if not values:
        return "n/a"
    mean = statistics.fmean(values)
    if len(values) == 1:
        return f"{mean:.0%}"
    return f"{mean:.0%} ({min(values):.0%}–{max(values):.0%})"


def weight_grid(step: float) -> np.ndarray:
    """Every weight vector over SHOOT_AXES on a `step` grid that sums to 1."""
    units = round(1 / step)
    if abs(units * step - 1) > 1e-9:
        raise ValueError(f"--step must divide 1 evenly, got {step}")
    k = len(SHOOT_AXES)
    rows = []
    for bars in itertools.combinations(range(units + k - 1), k - 1):
        edges = (-1, *bars, units + k - 1)
        rows.append([edges[i + 1] - edges[i] - 1 for i in range(k)])
    return np.array(rows, dtype=np.float64) / units


def mean_scores(runs: list[list[dict | None]]) -> list[dict | None]:
    """Average each photo's axis scores across runs (sets missing from any run are dropped)."""
    out = []
    for per_run in zip(*runs, strict=True):
        if any(g is None for g in per_run):
            out.append(None)
            continue
        photos = []
        for rows in zip(*(g["photos"] for g in per_run), strict=True):
            row = dict(rows[0])
            for field in (*SHOOT_AXES, "relative_rank"):
                row[field] = statistics.fmean(r[field] for r in rows)
            photos.append(row)
        out.append({"photos": photos})
    return out


def sweep_weights(truth: list[dict], scored: list[dict | None], step: float) -> dict:
    """
    Coarse grid over SHOOT_WEIGHTS: oracle top-1, then top-3.

    Reports the plateau (every candidate within one set of the best top-1)
    rather than a single winner, because ~20 sets cannot separate candidates
    more finely than that.
    """
    current = np.array([[SHOOT_WEIGHTS[a] for a in SHOOT_AXES]])
    grid = np.vstack([weight_grid(step), current])      # last row: today's weights
    top1 = np.zeros(len(grid))
    top3 = np.zeros(len(grid))
    n = 0
    for entry, group in zip(truth, scored, strict=True):
        if group is None:
            continue
        n += 1
        photos = group["photos"]
        axes   = np.array([[p[a] for a in SHOOT_AXES] for p in photos], dtype=np.float64)
        # Same tiebreak as rank_group: lower relative_rank wins a tie.
        tiebreak = np.array([p["relative_rank"] for p in photos]) * 1e-6
        scores   = grid @ axes.T - tiebreak                     # candidates x photos
        pick     = [p["photo_id"] for p in photos].index(entry["pick"])
        better   = (scores > scores[:, [pick]]).sum(axis=1)
        top1 += better == 0
        top3 += better <= 2
    if n == 0:
        return {"sets": 0}

    current_top1 = top1[-1]
    grid, top1, top3 = grid[:-1], top1[:-1], top3[:-1]
    best_top1 = top1.max()
    plateau   = top1 >= best_top1 - 1
    best      = np.lexsort((-top3, -top1))[0]
    return {
        "sets":          n,
        "candidates":    len(grid),
        "best_top1":     best_top1 / n,
        "best_top3":     top3[best] / n,
        "best_weights":  dict(zip(SHOOT_AXES, grid[best].round(4).tolist(), strict=True)),
        "plateau_share": float(plateau.mean()),
        "plateau_range": {
            axis: (float(grid[plateau, i].min()), float(np.median(grid[plateau, i])),
                   float(grid[plateau, i].max()))
            for i, axis in enumerate(SHOOT_AXES)
        },
        "current_top1":  float(current_top1 / n),
        "current_on_plateau": bool(current_top1 >= best_top1 - 1),
    }


def diagnostics(truth: list[dict], scored: list[dict | None]) -> dict:
    photos = [p for g in scored if g for p in g["photos"]]
    picks, all_face_sets = [], []
    for entry, group in zip(truth, scored, strict=True):
        if group is None:
            continue
        picks.append(next(p for p in group["photos"] if p["photo_id"] == entry["pick"]))
        if all(p["face_detected"] for p in group["photos"]):
            all_face_sets.append((entry, group))

    latency: dict[int, list[float]] = {}
    for group in scored:
        if group and "gemini_latency_s" in group:
            latency.setdefault(len(group["photos"]), []).append(group["gemini_latency_s"])

    face_only = pick_metrics([e for e, _ in all_face_sets], [g for _, g in all_face_sets])
    return {
        "photos":               len(photos),
        "face_missed":          sum(1 for p in photos if not p["face_detected"]),
        "picks":                len(picks),
        "picks_without_face":   sum(1 for p in picks if not p["face_detected"]),
        "all_face_sets":        len(all_face_sets),
        "all_face_top1":        face_only.get("top1"),
        "below_gate":           sum(1 for p in photos if p["blur_raw"] < V1_BLUR_GATE),
        "picks_below_gate":     sum(1 for p in picks if p["blur_raw"] < V1_BLUR_GATE),
        "pick_blur_raw":        _quantiles([p["blur_raw"] for p in picks]),
        "latency":              {n: (len(v), statistics.fmean(v), max(v)) for n, v in sorted(latency.items())},
    }


def e2e_metrics(truth: list[dict], predicted: list[list[str]], scored: list[dict | None]) -> dict:
    """
    §12.3(b). A true set is a hit when the predicted group holding its pick
    chose that pick. A true set has no winner of its own when no predicted
    winner falls inside it: that is a shortlist hole.
    """
    winners: dict[int, str] = {}
    group_of: dict[str, int] = {}
    for i, (ids, group) in enumerate(zip(predicted, scored, strict=True)):
        for pid in ids:
            group_of[pid] = i
        if group is not None:
            winners[i] = rank_group(group["photos"])[0]["photo_id"]

    hits = holes = evaluated = 0
    for entry in truth:
        g = group_of[entry["pick"]]
        if g not in winners:
            continue
        evaluated += 1
        hits += winners[g] == entry["pick"]
        holes += not any(w in entry["photo_ids"] for w in winners.values())
    return {
        "sets":      evaluated,
        "hit_rate":  hits / evaluated if evaluated else None,
        "holes":     holes,
        "failed_groups": sum(1 for g in scored if g is None),
    }


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return [*lines, ""]


def report_grouping(lines, ordered_ids, measured, truth_sets, order, do_sweep) -> list[list[str]]:
    truth     = [s["photo_ids"] for s in truth_sets]
    grouping  = group_measured(ordered_ids, measured, None, order)
    predicted = [g["photo_ids"] for g in grouping["groups"]]
    m         = boundary_metrics(predicted, truth)

    lines += ["## Grouping (§12.2)", ""]
    if order != "capture_time":
        lines += [("**Capture times were missing on at least one photo, so the shoot was "
                  "ordered by filename.**"), ""]
    sizes = [len(s) for s in truth]
    lines += _table(["Metric", "Value", "Target"], [
        ["True sets / predicted sets", f"{len(truth)} / {len(predicted)}", ""],
        ["Boundary precision", f"{m['precision']:.2f}", ""],
        ["Boundary recall", f"{m['recall']:.2f}", ""],
        ["Boundary F1", f"{m['f1']:.2f}", "≥ 0.90"],
        ["**Corrections needed**", (f"**{m['corrections']}** ({m['false_boundaries']} merges, "
                                   f"{m['missed_boundaries']} splits)"), "≤ 3"],
        ["Exact-set match", f"{m['exact_set_match']:.0%}", "≥ 80%"],
        ["±1 tolerant F1", f"{m['tolerant_f1']:.2f}", "reported"],
        ["True sets larger than MAX_GROUP", sum(1 for n in sizes if n > THRESHOLDS["MAX_GROUP"]), ""],
        ["True sets of one photo", sum(1 for n in sizes if n == 1), ""],
    ])

    true = boundaries_of(truth)
    lines += ["### Distances: true boundaries vs same-set pairs", "",
              "min / 25% / median / 75% / max", ""]
    rows = []
    for name, field in (("dt (s)", "dt"), ("dh (bits)", "dh"), ("dc", "dc")):
        at   = [p[field] for i, p in enumerate(measured) if i in true and p[field] is not None]
        away = [p[field] for i, p in enumerate(measured) if i not in true and p[field] is not None]
        rows.append([name, _quantiles(at), _quantiles(away)])
    lines += _table(["Distance", f"True boundary ({len(true)} pairs)",
                     f"Same set ({len(measured) - len(true)} pairs)"], rows)

    wrong = [(i, p) for i, p in enumerate(grouping["pairs"]) if p["final_boundary"] != (i in true)]
    if wrong:
        lines += ["### Wrong decisions with the default thresholds", ""]
        lines += _table(["Between", "Error", "dt (s)", "dh", "dc", "Rule"], [
            [f"{_stem(p['a'])} → {_stem(p['b'])}",
             "false boundary" if p["final_boundary"] else "missed boundary",
             "n/a" if p["dt"] is None else f"{p['dt']:.1f}",
             p["dh"], "n/a" if p["dc"] is None else f"{p['dc']:.3f}",
             p["final_reason"] or p["rule"]]
            for _, p in wrong
        ])

    if do_sweep:
        print("[eval] sweeping grouping thresholds...", file=sys.stderr)
        sweep = sweep_thresholds(ordered_ids, measured, truth, order)
        lines += [
            "### Threshold sweep", "",
            (f"{sweep['combos']:,} combinations. Best: **{sweep['best_corrections']} corrections** "
            f"({sweep['at_best']:,} combinations reach it; {sweep['near_best']:,} are within one). "
            f"Defaults: {m['corrections']}."), "",
            ("Per value: the best corrections any combination with that value reaches, and the "
            "share of its combinations within one of the overall best. Prefer values with a high "
            "share (a plateau) over a single best point."), "",
        ]
        rows = []
        for name, values in sweep["marginals"].items():
            for v in values:
                mark = " (default)" if THRESHOLDS[name] == v["value"] else ""
                rows.append([name, f"{v['value']}{mark}", v["best"], f"{v['near_share']:.0%}"])
        lines += _table(["Threshold", "Value", "Best corrections", "Within one of best"], rows)
        lines += ["Example best combinations:", ""]
        lines += [f"- `{json.dumps(example)}`" for example in sweep["best_examples"]] + [""]
        lines += [(f"Best combination closest to the defaults ({sweep['closest_changes']} "
                   f"change(s)): `{json.dumps(_changed(sweep['closest_best']))}`"), ""]

        print("[eval] comparing fixed and adaptive time rules...", file=sys.stderr)
        report_time_rules(lines, compare_time_rules(ordered_ids, measured, truth, order), true)
    return predicted


def _changed(combo: dict) -> dict:
    """The entries of a threshold combination that differ from the defaults."""
    defaults = {**THRESHOLDS, **ADAPTIVE_DEFAULTS}
    return {k: v for k, v in combo.items() if k == "TIME_RULE" or defaults[k] != v}


def report_time_rules(lines: list[str], cmp: dict, true: set[int]) -> None:
    shared = ", ".join(TIME_RULE_SHARED)
    lines += [
        "### Fixed vs adaptive time rule (§7.3)", "",
        (f"Both rules are swept over the same grid of {shared}; the other thresholds stay at "
         "their defaults. The fixed rule tries T_SOFT values. The adaptive rule replaces T_SOFT "
         "with a pause of max(T_MIN, ADAPT_K × median dt of the last ADAPT_N same-set pairs). "
         "Each row's combination is the best one that changes the fewest defaults."), "",
    ]
    rows = []
    for name, label in (("default", "Defaults"), ("fixed", "Best fixed"), ("adaptive", "Best adaptive")):
        r, m = cmp[name], cmp[name]["metrics"]
        sweep = r.get("sweep")
        rows.append([
            label,
            "–" if not sweep else f"{sweep['combos']:,}",
            "–" if not sweep else f"{sweep['near_best'] / sweep['combos']:.0%}",
            "–" if not sweep else f"`{json.dumps(_changed(r['chosen']))}`",
            f"**{m['corrections']}** ({m['false_boundaries']} merges, {m['missed_boundaries']} splits)",
            f"{m['precision']:.2f}", f"{m['recall']:.2f}", f"{m['f1']:.2f}",
        ])
    lines += _table(["Rule", "Combinations", "Within one of best", "Changes from defaults",
                     "Corrections", "Precision", "Recall", "F1"], rows)

    def wrong(name: str) -> dict[int, dict]:
        pairs = cmp[name]["grouping"]["pairs"]
        return {i: p for i, p in enumerate(pairs) if p["final_boundary"] != (i in true)}

    errors = {name: wrong(name) for name in ("default", "fixed", "adaptive")}
    pairs  = cmp["adaptive"]["grouping"]["pairs"]      # carries t_pause for every pair
    lines += [("Every pair that any of the three gets wrong. ✓ is a correct decision; "
               "the adaptive column shows the pause threshold the pair was held to."), ""]
    def cell(name: str, i: int) -> str:
        if i not in errors[name]:
            return "✓"
        return "false boundary" if i not in true else "missed"

    rows = []
    for i in sorted(set().union(*errors.values())):
        p = pairs[i]
        rows.append([
            f"{_stem(p['a'])} → {_stem(p['b'])}",
            "new set" if i in true else "same set",
            "n/a" if p["dt"] is None else f"{p['dt']:.0f}", p["dh"],
            "n/a" if p["dc"] is None else f"{p['dc']:.3f}",
            cell("default", i), cell("fixed", i), f"{cell('adaptive', i)} (pause {p['t_pause']:.0f} s)",
        ])
    lines += _table(["Between", "Truth", "dt (s)", "dh", "dc", "Defaults", "Best fixed",
                     "Best adaptive"], rows)

    lines += [("Time knobs: the best corrections each value reaches, and the share of its "
               "combinations within one of that rule's best."), ""]
    rows = []
    for name, grid in (("fixed", FIXED_TIME_GRID), ("adaptive", ADAPTIVE_TIME_GRID)):
        for knob in grid:
            if knob == "TIME_RULE":
                continue
            for v in cmp[name]["sweep"]["marginals"][knob]:
                rows.append([name, knob, v["value"], v["best"], f"{v['near_share']:.0%}"])
    lines += _table(["Rule", "Knob", "Value", "Best corrections", "Within one of best"], rows)


def report_oracle(lines, input_dir, truth_sets, cache, runs, spreads, step, cached_only=False) -> None:
    sets = [s["photo_ids"] for s in truth_sets]
    scored = {
        spread: [score_sets(input_dir, sets, cache, spread, run, cached_only) for run in range(runs)]
        for spread in spreads
    }

    lines += ["## Picks with oracle grouping (§12.3a)", ""]
    if cached_only:
        lines += ["Cached scores only: no Gemini calls were made for this report.", ""]
    for spread, per_run in scored.items():
        label = "on" if spread else "off"
        for run, groups in enumerate(per_run, 1):
            failed = [s["set"] for s, g in zip(truth_sets, groups, strict=True) if g is None]
            sliced = [s["set"] for s, g in zip(truth_sets, groups, strict=True)
                      if g is not None and "sliced_from" in g]
            if failed:
                why = ("are not in the cache" if cached_only
                       else "could not be scored. Re-run to retry")
                lines += [(f"**Spread {label}, run {run}: {len(failed)} of {len(sets)} sets "
                          f"{why} and are excluded (sets {', '.join(map(str, failed))}).**"), ""]
            if sliced:
                lines += [(f"Spread {label}, run {run}: sets {', '.join(map(str, sliced))} were "
                          "split after scoring, so they are re-ranked from the cached group "
                          "that holds them (§10.4)."), ""]

    rows = []
    for spread, per_run in scored.items():
        for name, weights in ablation_weights().items():
            ms = [pick_metrics(truth_sets, run, weights) for run in per_run]
            ms = [m for m in ms if m["sets"]]
            if not ms:
                continue
            rows.append([
                name, "on" if spread else "off",
                _mean_range([m["top1"] for m in ms]), _mean_range([m["top3"] for m in ms]),
                f"{statistics.fmean(m['mrr'] for m in ms):.2f}",
                _mean_range([m["lenient_top1"] for m in ms]),
            ])
    baseline = statistics.fmean(1 / len(s) for s in sets)
    lines += [(f"Mean (min–max) over {runs} run(s). Targets: top-1 ≥ 50%, top-3 ≥ 80%. "
              f"Random top-1 baseline: {baseline:.0%}. A difference only counts if it is "
              "larger than the min–max range."), ""]
    lines += _table(["Weights", "Forced spread", "Top-1", "Top-3", "MRR", "Lenient top-1"], rows)
    lines += [("\"Gemini axes only\" is the four Gemini axes of SHOOT_WEIGHTS rescaled to sum "
              "to 1."), ""]

    report_weight_candidates(lines, truth_sets, scored[spreads[0]], spreads[0])

    primary = scored[spreads[0]]
    averaged = mean_scores(primary)
    sweep = sweep_weights(truth_sets, averaged, step)
    if sweep["sets"]:
        lines += [
            (f"### Weight sweep (step {step}, forced spread {'on' if spreads[0] else 'off'}, "
            f"scores averaged over {runs} run(s))"), "",
            (f"{sweep['candidates']:,} candidates over {sweep['sets']} sets. Best top-1 "
            f"{sweep['best_top1']:.0%} (top-3 {sweep['best_top3']:.0%}). "
            f"{sweep['plateau_share']:.0%} of candidates are within one set of the best, so "
            "read the ranges below rather than a single winner."), "",
        ]
        where = "on" if sweep["current_on_plateau"] else "off"
        lines += [(f"Current SHOOT_WEIGHTS: top-1 {sweep['current_top1']:.0%}, {where} the "
                  "plateau."), ""]
        lines += _table(["Axis", "Current", "Plateau min", "Plateau median", "Plateau max", "One best"], [
            [axis, SHOOT_WEIGHTS[axis], f"{lo:.2f}", f"{mid:.2f}", f"{hi:.2f}",
             sweep["best_weights"][axis]]
            for axis, (lo, mid, hi) in sweep["plateau_range"].items()
        ])

    d = diagnostics(truth_sets, primary[0])
    if d["photos"]:
        lines += ["### Diagnostics (§12.4, run 1)", ""]
        top1 = "n/a" if d["all_face_top1"] is None else f"{d['all_face_top1']:.0%}"
        lines += _table(["Diagnostic", "Value"], [
            ["Photos with no face detected", (f"{d['face_missed']} of {d['photos']} "
                                             f"({d['face_missed'] / d['photos']:.0%})")],
            ["Key picks with no face detected", f"{d['picks_without_face']} of {d['picks']}"],
            ["Top-1 on sets where every face was detected", f"{top1} (sets: {d['all_face_sets']})"],
            [f"Photos the v1 blur gate (blur_raw < {V1_BLUR_GATE:.0f}) would drop",
             f"{d['below_gate']} of {d['photos']}"],
            ["Key picks the v1 blur gate would drop", f"{d['picks_below_gate']} of {d['picks']}"],
            ["blur_raw of key picks (min / 25% / median / 75% / max)", d["pick_blur_raw"]],
        ])
        if d["latency"]:
            lines += [("Gemini latency by set size (one call per set). This decides whether a "
                      "synchronous per-set request fits under Cloudflare's 100 s limit "
                      "(V2-REVIEW J9)."), ""]
            lines += _table(["Photos in set", "Calls", "Mean (s)", "Max (s)"], [
                [n, calls, f"{mean:.1f}", f"{worst:.1f}"]
                for n, (calls, mean, worst) in d["latency"].items()
            ])


def _describe_weights(weights: dict[str, float]) -> str:
    return ", ".join(f"{a} {w:g}" for a, w in weights.items() if w > 0)


def candidate_metrics(truth_sets: list[dict], per_run: list[list[dict | None]]) -> dict:
    """
    WEIGHT_CANDIDATES (and Gemini's relative_rank) over the sets scored in
    every run, so every candidate and run is measured on the same sets.

    Returns {"sets", "rows": [{"weights", "axes", "top1", "top3", "mrr",
    "within_range"}], "relative_rank", "simplest"}. top1 and top3 are
    per-run lists. A candidate is within range when, on both top-1 and
    top-3, its min–max range over runs overlaps that of the candidate with
    the best mean: the difference is no larger than Gemini's own noise.
    "simplest" is the within-range candidate with the fewest axes, then the
    best mean top-3 (the results UI shows each set's top 3), then top-1.
    """
    common = [i for i in range(len(truth_sets)) if all(run[i] is not None for run in per_run)]
    truth  = [truth_sets[i] for i in common]
    runs   = [[run[i] for i in common] for run in per_run]
    if not truth:
        return {"sets": 0}

    rows = []
    for weights in WEIGHT_CANDIDATES:
        ms = [pick_metrics(truth, run, weights) for run in runs]
        rows.append({
            "weights": weights,
            "axes":    sum(1 for w in weights.values() if w > 0),
            "top1":    [m["top1"] for m in ms],
            "top3":    [m["top3"] for m in ms],
            "mrr":     statistics.fmean(m["mrr"] for m in ms),
        })
    floor = {
        metric: min(max(rows, key=lambda r, m=metric: statistics.fmean(r[m]))[metric])
        for metric in ("top1", "top3")
    }
    for r in rows:
        r["within_range"] = all(max(r[m]) >= floor[m] - 1e-9 for m in ("top1", "top3"))
    simplest = min(
        (r for r in rows if r["within_range"]),
        key=lambda r: (r["axes"], -statistics.fmean(r["top3"]), -statistics.fmean(r["top1"])),
    )

    rr = [relative_rank_metrics(truth, run) for run in runs]
    return {
        "sets":          len(truth),
        "rows":          rows,
        "simplest":      simplest,
        "relative_rank": {"top1": [m["top1"] for m in rr], "top3": [m["top3"] for m in rr],
                          "mrr": statistics.fmean(m["mrr"] for m in rr)},
    }


def report_weight_candidates(lines, truth_sets, per_run, spread) -> None:
    c = candidate_metrics(truth_sets, per_run)
    if not c["sets"]:
        return
    lines += [
        f"### Weight candidates (forced spread {'on' if spread else 'off'})", "",
        (f"A few simple, round weight sets, each scored on the {c['sets']} sets that have scores "
         f"in all {len(per_run)} run(s). Mean (min–max) over runs. \"Within range\" means the "
         "min–max range overlaps the best candidate's on both top-1 and top-3, so the "
         "difference from the best is no larger than Gemini's own run-to-run range."), "",
    ]
    rows = []
    for r in c["rows"]:
        label = _describe_weights(r["weights"])
        if r["weights"] == SHOOT_WEIGHTS:
            label = f"current SHOOT_WEIGHTS: {label}"
        rows.append([label, r["axes"], _mean_range(r["top1"]), _mean_range(r["top3"]),
                     f"{r['mrr']:.2f}", "yes" if r["within_range"] else ""])
    rr = c["relative_rank"]
    rows.append(["Gemini relative_rank alone (no weights)", "–", _mean_range(rr["top1"]),
                 _mean_range(rr["top3"]), f"{rr['mrr']:.2f}", ""])
    lines += _table(["Weights", "Axes", "Top-1", "Top-3", "MRR", "Within range"], rows)
    lines += [(f"Simplest candidate within range: **{_describe_weights(c['simplest']['weights'])}** "
               f"({c['simplest']['axes']} axes)."), ""]


def report_e2e(lines, input_dir, truth_sets, predicted, cache, spread, cached_only=False) -> None:
    scored = score_sets(input_dir, predicted, cache, spread, 0, cached_only)
    m      = e2e_metrics(truth_sets, predicted, scored)
    oracle = pick_metrics(truth_sets, score_sets(input_dir, [s["photo_ids"] for s in truth_sets],
                                                 cache, spread, 0, cached_only))
    lines += ["## End to end (§12.3b)", "",
              ("Predicted groups (default thresholds), then predicted winners. Run 1, forced "
              f"spread {'on' if spread else 'off'}."), ""]
    rows = [
        ["True sets whose pick was chosen",
         "n/a" if m["hit_rate"] is None else f"{m['hit_rate']:.0%} of {m['sets']}"],
        ["True sets with no winner of their own (shortlist holes)", m["holes"]],
    ]
    if oracle.get("sets"):
        rows.append(["Oracle top-1 for comparison (target: within 10 points)", f"{oracle['top1']:.0%}"])
    if m["failed_groups"]:
        what = ("not in the cache (cached-only run; their true sets are skipped)" if cached_only
                else "that could not be scored")
        rows.append([f"**Predicted groups {what}**", m["failed_groups"]])
    lines += _table(["Metric", "Value"], rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate full shoot mode against an answer key")
    parser.add_argument("--input", "-i", required=True, help="Folder of the shoot's photos")
    parser.add_argument("--key", "-k", required=True, help="answer_key.json (see eval/README.md)")
    parser.add_argument("--stage", default="all", choices=["grouping", "oracle", "e2e", "all"])
    parser.add_argument("--cache", default=None,
                        help="Score cache file (default: output/eval_<shoot>_scores.json)")
    parser.add_argument("--runs", type=int, default=3,
                        help="Oracle scoring runs, to measure Gemini's run-to-run range (default 3)")
    parser.add_argument("--spread", default="both", choices=["on", "off", "both"],
                        help="Forced-spread prompt variants to score (default both; Q6)")
    parser.add_argument("--step", type=float, default=0.10,
                        help="Weight sweep grid step (default 0.10; 0.05 is ~888k candidates)")
    parser.add_argument("--no-sweep", action="store_true", help="Skip the threshold sweep")
    parser.add_argument("--cached-only", action="store_true",
                        help=("Never call Gemini: use cached scores only. A set split after "
                              "scoring is re-ranked from the cached group that holds it; "
                              "other uncached sets are excluded"))
    parser.add_argument("--report", default=None,
                        help="Markdown report path (default: output/eval_<shoot>_report.md)")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    try:
        key   = load_key(args.key)
        shoot = key.get("shoot", "shoot")
        cache = Cache(args.cache or f"output/eval_{shoot}_scores.json")
        ordered, order = order_photos(load_features(args.input, cache))
        ordered_ids    = [f["photo_id"] for f in ordered]
        truth_sets     = resolve_key(key, ordered_ids)
    except (ValueError, OSError) as e:
        print(f"[eval] error: {e}", file=sys.stderr)
        return 1

    measured = measure_pairs(ordered)
    spreads  = {"on": [True], "off": [False], "both": [True, False]}[args.spread]
    sources  = sorted({f["time_source"] for f in ordered})

    lines = [
        f"# Shoot evaluation: {shoot}", "",
        (f"{len(ordered_ids)} photos, {len(truth_sets)} labelled sets. Order: {order}. "
        f"Time sources: {', '.join(sources)}. Stage: {args.stage}."), "",
    ]
    predicted = None
    if args.stage in ("grouping", "e2e", "all"):
        predicted = report_grouping(lines, ordered_ids, measured, truth_sets, order,
                                    do_sweep=not args.no_sweep and args.stage != "e2e")
    if args.stage in ("oracle", "all"):
        report_oracle(lines, args.input, truth_sets, cache, args.runs, spreads, args.step,
                      args.cached_only)
    if args.stage in ("e2e", "all"):
        report_e2e(lines, args.input, truth_sets, predicted, cache, spreads[0], args.cached_only)

    report = Path(args.report or f"output/eval_{shoot}_report.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines) + "\n")
    print(f"[eval] report written to {report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
