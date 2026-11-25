"""
Full shoot mode pipeline (docs/V2-PLAN.md §6): group a shoot into sets, then
score every set with both signal families and pick a winner per set.

  per group:
    compute_burst_scores() per photo    sharpness, exposure, face axes, raws
    score_photos(..., batch_size=n)     ONE Gemini call for the whole group
    merge -> rank with SHOOT_WEIGHTS

There is no blur gate: full-image blur_raw is low on good shallow
depth-of-field portraits, so the v1 gate would drop the frames we want.

A group is atomic. It is either fully scored or "failed"; default scores are
never assigned, and one failed group does not stop the others.

Scoring (score_group) and weighting (rank_group) are separate so the
evaluation harness can cache raw scores and re-rank under other weights
without calling Gemini again.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

from core.profiles import (
    SHOOT_AXES,
    SHOOT_AXIS_SOURCES,
    SHOOT_WEIGHTS,
    validate_shoot_weights,
)

_BURST_FIELDS = (
    "sharpness", "exposure", "blur_raw", "tenengrad_raw",
    "face_detected", "face_sharpness", "face_exposure", "face_blur_raw",
)
_GEMINI_FIELDS = (
    "subject_1_expression", "subject_2_expression", "expression",
    "camera_engagement", "composition", "subject_focus", "relative_rank", "notes",
)


def score_group(photos: list[dict], force_spread: bool = True) -> dict:
    """
    Score one group with both signal families. No weights are applied.

    Args:
        photos:       [{"photo_id", "path"}, ...] in shooting order. `path` is
                      the compressed copy from ingest().
        force_spread: keep Gemini's forced score-spread rule. Ignored for a
                      one-photo group, where the rule cannot be satisfied.

    Returns:
        {"photos": [raw per-photo scores], "gemini_latency_s": float}

    Raises:
        ValueError / RuntimeError / EnvironmentError if any photo cannot be
        scored. The caller marks the whole group failed.
    """
    from core.score_burst import compute_burst_scores
    from core.score_vision import score_photos

    burst = [compute_burst_scores(p["path"], photo_id=p["photo_id"]) for p in photos]

    t0 = time.perf_counter()
    gemini = score_photos(
        [p["path"] for p in photos],
        photo_ids=[p["photo_id"] for p in photos],
        batch_size=len(photos),
        prompt_mode="shoot",
        force_spread=force_spread and len(photos) > 1,
    )
    latency = time.perf_counter() - t0

    gemini_by_id = {g["photo_id"]: g for g in gemini}
    missing = [b["photo_id"] for b in burst if b["photo_id"] not in gemini_by_id]
    if missing or len(gemini_by_id) != len(burst):
        raise RuntimeError(
            f"Gemini returned scores for {len(gemini_by_id)} of {len(burst)} photos "
            f"in the group (missing: {missing})"
        )

    merged = []
    for b in burst:
        g = gemini_by_id[b["photo_id"]]
        row = {"photo_id": b["photo_id"]}
        row.update({k: b[k] for k in _BURST_FIELDS})
        row.update({k: g[k] for k in _GEMINI_FIELDS})
        merged.append(row)

    return {"photos": merged, "gemini_latency_s": round(latency, 2)}


def rank_group(scored: list[dict], weights: dict[str, float] | None = None) -> list[dict]:
    """
    Apply shoot weights to one group's raw scores.

    Returns new per-photo dicts (the input is not modified) sorted by
    final_score desc, with Gemini's relative_rank as the tiebreaker. Each
    carries the full score_breakdown in the same shape as set mode.
    """
    w = SHOOT_WEIGHTS if weights is None else weights
    validate_shoot_weights(w)

    ranked = []
    for src in scored:
        s = dict(src)
        s["final_score"] = round(sum(s[axis] * w[axis] for axis in SHOOT_AXES), 3)
        s["score_breakdown"] = {
            axis: {
                "raw":              s[axis],
                "weight":           w[axis],
                "effective_weight": w[axis],
                "contribution":     round(s[axis] * w[axis], 3),
                "source":           SHOOT_AXIS_SOURCES[axis],
            }
            for axis in SHOOT_AXES
        }
        ranked.append(s)

    ranked.sort(key=lambda x: (-x["final_score"], x.get("relative_rank", 999)))
    for i, s in enumerate(ranked, 1):
        s["final_rank"] = i
    return ranked


def group_result(group_id: str, ranked: list[dict]) -> dict:
    """The §9.5 group object for a scored group."""
    return {
        "group_id":            group_id,
        "status":              "scored",
        "photo_count":         len(ranked),
        "face_detected_count": sum(1 for r in ranked if r["face_detected"]),
        "winner_photo_id":     ranked[0]["photo_id"],
        "ranked":              ranked,
    }


def run_shoot(
    photos: list[dict],
    force_spread: bool = True,
    thresholds: dict | None = None,
    weights: dict[str, float] | None = None,
) -> dict:
    """
    Group and score a whole shoot.

    Args:
        photos: ingest() output ({"photo_id", "original_path", "path"}).

    Returns the shoot output: grouping (§7.5) plus one §9.5 group object per
    group, in shooting order. Failed groups are listed with their error.
    """
    from core.group import extract_features, group_photos, public_grouping
    from core.score_vision import MAX_CONCURRENCY

    w = SHOOT_WEIGHTS if weights is None else weights
    validate_shoot_weights(w)

    print(f"[shoot] reading capture times and fingerprints ({len(photos)} photos)...", file=sys.stderr)
    grouping = group_photos(extract_features(photos), thresholds)
    groups   = grouping["groups"]
    print(f"[shoot] found {len(groups)} set(s), order={grouping['order']}", file=sys.stderr)

    by_id = {p["photo_id"]: p for p in photos}

    def _run(group: dict) -> dict:
        members = [by_id[pid] for pid in group["photo_ids"]]
        try:
            scored = score_group(members, force_spread=force_spread)
        except Exception as e:  # noqa: BLE001 - any failure fails this group only, loudly
            print(f"[shoot] {group['group_id']} FAILED: {e}", file=sys.stderr)
            return {
                "group_id":    group["group_id"],
                "status":      "failed",
                "photo_count": len(members),
                "error":       {"code": "scoring_failed", "message": str(e)},
            }
        result = group_result(group["group_id"], rank_group(scored["photos"], w))
        result["gemini_latency_s"] = scored["gemini_latency_s"]
        print(
            f"[shoot] {group['group_id']} scored ({len(members)} photos, "
            f"gemini {scored['gemini_latency_s']:.1f}s)",
            file=sys.stderr,
        )
        return result

    with ThreadPoolExecutor(max_workers=max(1, min(len(groups), MAX_CONCURRENCY))) as pool:
        results = list(pool.map(_run, groups))

    failed = sum(1 for r in results if r["status"] == "failed")
    return {
        "mode":          "shoot",
        "weights":       w,
        "force_spread":  force_spread,
        "total_photos":  len(photos),
        "groups_total":  len(groups),
        "groups_scored": len(groups) - failed,
        "groups_failed": failed,
        "grouping":      public_grouping(grouping),
        "groups":        results,
    }


def print_summary(output: dict) -> None:
    """Human-readable summary on stderr: one line per set, with its boundary reason."""
    by_group = {g["group_id"]: g for g in output["grouping"]["groups"]}
    print("\n=== PhotoRank Results (shoot mode) ===\n", file=sys.stderr)
    for result in output["groups"]:
        before = by_group[result["group_id"]]["boundary_before"]
        reason = before["reason"]
        if before.get("dt_s") is not None:
            reason += f", {before['dt_s']:.0f}s gap"
        head = f"  {result['group_id']}  {result['photo_count']:>2} photos  [{reason}]"
        if result["status"] == "failed":
            print(f"{head}  FAILED: {result['error']['message'][:200]}", file=sys.stderr)
            continue
        winner = result["ranked"][0]
        print(
            f"{head}  pick={winner['photo_id']}  score={winner['final_score']:.3f}"
            f"  — {winner['notes']}",
            file=sys.stderr,
        )
    if output["groups_failed"]:
        print(
            f"\n  {output['groups_failed']} of {output['groups_total']} set(s) FAILED "
            "and have no pick. Re-run to retry.",
            file=sys.stderr,
        )
    print(file=sys.stderr)
