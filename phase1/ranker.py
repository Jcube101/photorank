"""
CLI entry point for Phase 1 photo ranking pipeline.

Two-layer scoring architecture:
  Layer 1 — deterministic (blur_filter.py, OpenCV + MediaPipe):
    sharpness, exposure, eye_openness
  Layer 2 — semantic (scorer.py, Gemini 1.5 Flash):
    expression, composition, subject_focus

Pipeline:
  1. Collect images from input directory
  2. Compute deterministic scores for all images (also yields blur_raw for gate)
  3. Exclude images below blur threshold (never sent to Gemini)
  4. Send sharp images to Gemini for semantic scoring
  5. Merge layers by photo_id
  6. Apply profile weights; redistribute eye_openness weight when no face found
  7. Output ranked JSON to stdout

Usage:
  python phase1/ranker.py --input /path/to/photos --profile family
  python phase1/ranker.py --input /path/to/photos --profile portrait --blur-threshold 80
  python phase1/ranker.py --input /path/to/photos --profile custom \\
      --weights '{"sharpness":0.2,"exposure":0.1,"eye_openness":0.2,"expression":0.2,"composition":0.2,"subject_focus":0.1}'
"""

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Profiles — six axes, weights must sum to 1.0
#   Deterministic: sharpness, exposure, eye_openness
#   Semantic:      expression, composition, subject_focus
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict[str, float]] = {
    "family": {
        "expression":    0.25,
        "eye_openness":  0.20,
        "subject_focus": 0.20,
        "sharpness":     0.15,
        "composition":   0.12,
        "exposure":      0.08,
    },
    "portrait": {
        "eye_openness":  0.25,
        "sharpness":     0.20,
        "expression":    0.20,
        "subject_focus": 0.15,
        "exposure":      0.12,
        "composition":   0.08,
    },
    "event": {
        "composition":   0.25,
        "subject_focus": 0.20,
        "expression":    0.15,
        "sharpness":     0.15,
        "exposure":      0.15,
        "eye_openness":  0.10,
    },
}

AXES_DETERMINISTIC = ["sharpness", "exposure", "eye_openness"]
AXES_SEMANTIC      = ["expression", "composition", "subject_focus"]
ALL_AXES           = AXES_DETERMINISTIC + AXES_SEMANTIC


# ---------------------------------------------------------------------------
# Weight helpers
# ---------------------------------------------------------------------------

def validate_weights(weights: dict[str, float]) -> None:
    missing = set(ALL_AXES) - set(weights.keys())
    if missing:
        raise ValueError(f"Weights missing axes: {missing}")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")


def _effective_weights(
    weights: dict[str, float],
    eye_openness: float | None,
) -> dict[str, float]:
    """
    When eye_openness is None (no face detected), redistribute its weight
    proportionally across the remaining axes so the final score still sums
    to the full 1.0 range.
    """
    if eye_openness is not None or "eye_openness" not in weights:
        return weights

    eye_w     = weights["eye_openness"]
    remaining = {k: v for k, v in weights.items() if k != "eye_openness"}
    total     = sum(remaining.values())
    if total == 0:
        return remaining
    return {k: v * (1.0 + eye_w / total) for k, v in remaining.items()}


# ---------------------------------------------------------------------------
# Scoring and ranking
# ---------------------------------------------------------------------------

def _merge_scores(technical: dict, gemini: dict) -> dict:
    """Combine deterministic and Gemini score dicts for one photo."""
    return {
        "photo_id":     technical["photo_id"],
        "sharpness":    technical["sharpness"],
        "exposure":     technical["exposure"],
        "eye_openness": technical["eye_openness"],
        "expression":   gemini["expression"],
        "composition":  gemini["composition"],
        "subject_focus":gemini["subject_focus"],
        "notes":        gemini["notes"],
    }


def rank_photos(
    merged_scores: list[dict],
    weights: dict[str, float],
) -> list[dict]:
    """
    Apply profile weights to merged scores, produce final_score and final_rank.

    eye_openness weight is redistributed per-photo when no face was detected.
    Returns list sorted by final_score descending.
    """
    validate_weights(weights)

    for s in merged_scores:
        eff = _effective_weights(weights, s["eye_openness"])
        s["final_score"] = round(
            sum(s[axis] * eff[axis] for axis in eff if s.get(axis) is not None), 3
        )
        s["score_breakdown"] = {
            axis: {
                "raw":          s.get(axis),
                "weight":       weights.get(axis, 0),
                "effective_weight": round(eff.get(axis, 0), 4),
                "contribution": round(s[axis] * eff[axis], 3) if s.get(axis) is not None else 0,
                "source":       "deterministic" if axis in AXES_DETERMINISTIC else "gemini",
            }
            for axis in ALL_AXES
        }
        if s["eye_openness"] is None:
            s["score_breakdown"]["eye_openness"]["note"] = "no face detected — weight redistributed"

    ranked = sorted(merged_scores, key=lambda x: x["final_score"], reverse=True)
    for i, s in enumerate(ranked, 1):
        s["final_rank"] = i

    return ranked


# ---------------------------------------------------------------------------
# CLI output
# ---------------------------------------------------------------------------

def print_summary(ranked: list[dict], blurry: list[str]) -> None:
    print("\n=== PhotoRank Results ===\n", file=sys.stderr)
    for photo in ranked:
        eye_tag = "" if photo["eye_openness"] is not None else " [no face]"
        print(
            f"  #{photo['final_rank']:>2}  {photo['photo_id']:<40}"
            f"  score={photo['final_score']:.3f}{eye_tag}  — {photo['notes']}",
            file=sys.stderr,
        )
    if blurry:
        print(f"\n  Skipped (blurry): {len(blurry)} photo(s)", file=sys.stderr)
        for p in blurry:
            print(f"    {Path(p).name}", file=sys.stderr)
    print(file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PhotoRank Phase 1 CLI — deterministic scoring + Gemini ranking"
    )
    parser.add_argument("--input", "-i", required=True, help="Directory of photos to rank")
    parser.add_argument(
        "--profile", "-p",
        default="family",
        choices=list(PROFILES.keys()) + ["custom"],
        help="Scoring profile (default: family)",
    )
    parser.add_argument(
        "--weights", "-w",
        default=None,
        help=(
            "JSON weights for custom profile — must include all six axes: "
            "sharpness, exposure, eye_openness, expression, composition, subject_focus"
        ),
    )
    parser.add_argument(
        "--blur-threshold", "-b",
        type=float,
        default=100.0,
        help="Laplacian variance threshold; images below this are excluded (default: 100)",
    )
    parser.add_argument(
        "--no-blur-filter",
        action="store_true",
        help="Skip blur gate entirely (all images sent to Gemini)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Write JSON to file instead of stdout",
    )
    args = parser.parse_args()

    # Resolve weights
    if args.profile == "custom":
        if not args.weights:
            parser.error("--weights required when --profile custom")
        try:
            weights = json.loads(args.weights)
        except json.JSONDecodeError as e:
            parser.error(f"Invalid JSON in --weights: {e}")
        try:
            validate_weights(weights)
        except ValueError as e:
            parser.error(str(e))
    else:
        weights = PROFILES[args.profile]

    from phase1.blur_filter import collect_images, compute_technical_scores_batch
    from phase1.scorer import score_photos

    # Step 1: collect
    input_dir  = Path(args.input)
    print(f"[ranker] collecting images from {input_dir}...", file=sys.stderr)
    all_images = collect_images(input_dir)
    if not all_images:
        print("[ranker] no supported images found.", file=sys.stderr)
        sys.exit(1)
    print(f"[ranker] found {len(all_images)} image(s)", file=sys.stderr)

    # Step 2: deterministic scores for everything
    print("[ranker] computing deterministic scores (sharpness / exposure / eye openness)...", file=sys.stderr)
    all_technical = compute_technical_scores_batch(all_images)

    # Step 3: blur gate
    if args.no_blur_filter:
        sharp_technical = all_technical
        blurry_paths: list[str] = []
    else:
        sharp_technical = [t for t in all_technical if t["blur_raw"] >= args.blur_threshold]
        blurry_technical = [t for t in all_technical if t["blur_raw"] < args.blur_threshold]
        blurry_paths = [t["path"] for t in blurry_technical]
        print(
            f"[ranker] {len(sharp_technical)} sharp, {len(blurry_technical)} blurry "
            f"(threshold={args.blur_threshold})",
            file=sys.stderr,
        )

    if not sharp_technical:
        print(
            "[ranker] all images are below blur threshold. "
            "Lower --blur-threshold or use --no-blur-filter.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Step 4: Gemini semantic scoring (sharp images only)
    sharp_paths = [t["path"] for t in sharp_technical]
    print(
        f"[ranker] semantic scoring {len(sharp_paths)} photo(s) "
        f"via Gemini (profile={args.profile})...",
        file=sys.stderr,
    )
    gemini_scores = score_photos(sharp_paths, profile=args.profile)

    # Step 5: merge layers by photo_id
    gemini_by_id    = {s["photo_id"]: s for s in gemini_scores}
    technical_by_id = {t["photo_id"]: t for t in sharp_technical}

    merged: list[dict] = []
    for photo_id, tech in technical_by_id.items():
        if photo_id not in gemini_by_id:
            print(f"[ranker] warning: no Gemini score for {photo_id}, skipping", file=sys.stderr)
            continue
        merged.append(_merge_scores(tech, gemini_by_id[photo_id]))

    # Step 6: rank
    ranked = rank_photos(merged, weights)

    print_summary(ranked, blurry_paths)

    output_data = {
        "profile":        args.profile,
        "weights":        weights,
        "blur_threshold": None if args.no_blur_filter else args.blur_threshold,
        "total_photos":   len(all_images),
        "scored_photos":  len(merged),
        "skipped_blurry": len(blurry_paths),
        "ranked":         ranked,
    }

    output_json = json.dumps(output_data, indent=2)
    if args.output:
        Path(args.output).write_text(output_json)
        print(f"[ranker] results written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
