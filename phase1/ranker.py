"""
CLI entry point for Phase 1 photo ranking pipeline.

Full pipeline:
  1. Collect images from input directory
  2. Run blur filter, skip images below threshold
  3. Send remaining images to Gemini for scoring
  4. Apply profile weights to compute final scores
  5. Output ranked JSON to stdout

Usage:
  python phase1/ranker.py --input /path/to/photos --profile family
  python phase1/ranker.py --input /path/to/photos --profile portrait --blur-threshold 80
  python phase1/ranker.py --input /path/to/photos --profile custom \
      --weights '{"sharpness":0.4,"expression":0.3,"composition":0.1,"exposure":0.1,"subject_focus":0.1}'
"""

import argparse
import json
import sys
from pathlib import Path

PROFILES: dict[str, dict[str, float]] = {
    "family": {
        "expression": 0.35,
        "subject_focus": 0.25,
        "sharpness": 0.20,
        "composition": 0.12,
        "exposure": 0.08,
    },
    "portrait": {
        "sharpness": 0.30,
        "subject_focus": 0.25,
        "expression": 0.25,
        "exposure": 0.15,
        "composition": 0.05,
    },
    "event": {
        "composition": 0.30,
        "subject_focus": 0.25,
        "sharpness": 0.20,
        "exposure": 0.15,
        "expression": 0.10,
    },
}

AXES = ["sharpness", "expression", "composition", "exposure", "subject_focus"]


def validate_weights(weights: dict[str, float]) -> None:
    missing = set(AXES) - set(weights.keys())
    if missing:
        raise ValueError(f"Weights missing axes: {missing}")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")


def compute_final_score(score: dict, weights: dict[str, float]) -> float:
    return sum(score[axis] * weights[axis] for axis in AXES)


def rank_photos(
    scores: list[dict],
    weights: dict[str, float],
) -> list[dict]:
    """
    Apply weights to Gemini scores, add final_score and final_rank fields.
    Returns list sorted by final_score descending.
    """
    validate_weights(weights)
    for s in scores:
        s["final_score"] = round(compute_final_score(s, weights), 3)
        s["score_breakdown"] = {
            axis: {
                "raw": s[axis],
                "weight": weights[axis],
                "contribution": round(s[axis] * weights[axis], 3),
            }
            for axis in AXES
        }

    ranked = sorted(scores, key=lambda x: x["final_score"], reverse=True)
    for i, s in enumerate(ranked, 1):
        s["final_rank"] = i

    return ranked


def print_summary(ranked: list[dict], blurry: list[str]) -> None:
    print("\n=== PhotoRank Results ===\n", file=sys.stderr)
    for photo in ranked:
        print(
            f"  #{photo['final_rank']:>2}  {photo['photo_id']:<40}"
            f"  score={photo['final_score']:.3f}  — {photo['notes']}",
            file=sys.stderr,
        )
    if blurry:
        print(f"\n  Skipped (blurry): {len(blurry)} photo(s)", file=sys.stderr)
        for p in blurry:
            print(f"    {Path(p).name}", file=sys.stderr)
    print(file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PhotoRank Phase 1 CLI — blur filter + Gemini scoring + ranking"
    )
    parser.add_argument(
        "--input", "-i", required=True, help="Directory of photos to rank"
    )
    parser.add_argument(
        "--profile",
        "-p",
        default="family",
        choices=list(PROFILES.keys()) + ["custom"],
        help="Scoring profile (default: family)",
    )
    parser.add_argument(
        "--weights",
        "-w",
        default=None,
        help='JSON weights for custom profile, e.g. \'{"sharpness":0.4,...}\'',
    )
    parser.add_argument(
        "--blur-threshold",
        "-b",
        type=float,
        default=100.0,
        help="Laplacian variance threshold; images below this are skipped (default: 100)",
    )
    parser.add_argument(
        "--no-blur-filter",
        action="store_true",
        help="Skip blur filtering entirely",
    )
    parser.add_argument(
        "--output", "-o", default=None, help="Write JSON output to file instead of stdout"
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

    # Collect images
    from phase1.blur_filter import collect_images, filter_blurry
    from phase1.scorer import score_photos

    input_dir = Path(args.input)
    print(f"[ranker] collecting images from {input_dir}...", file=sys.stderr)
    all_images = collect_images(input_dir)

    if not all_images:
        print("[ranker] no supported images found.", file=sys.stderr)
        sys.exit(1)

    print(f"[ranker] found {len(all_images)} image(s)", file=sys.stderr)

    # Blur filter
    if args.no_blur_filter:
        sharp_paths = [str(p) for p in all_images]
        blurry_paths: list[str] = []
    else:
        print(
            f"[ranker] running blur filter (threshold={args.blur_threshold})...",
            file=sys.stderr,
        )
        sharp_paths, blurry_paths = filter_blurry(all_images, threshold=args.blur_threshold)
        print(
            f"[ranker] {len(sharp_paths)} sharp, {len(blurry_paths)} blurry",
            file=sys.stderr,
        )

    if not sharp_paths:
        print(
            "[ranker] all images are below blur threshold. "
            "Lower --blur-threshold or use --no-blur-filter.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Score
    print(
        f"[ranker] scoring {len(sharp_paths)} photo(s) with profile={args.profile}...",
        file=sys.stderr,
    )
    scores = score_photos(sharp_paths, profile=args.profile)

    # Rank
    ranked = rank_photos(scores, weights)

    # Print human-readable summary to stderr, JSON to stdout
    print_summary(ranked, blurry_paths)

    output_data = {
        "profile": args.profile,
        "weights": weights,
        "blur_threshold": args.blur_threshold if not args.no_blur_filter else None,
        "total_photos": len(all_images),
        "scored_photos": len(sharp_paths),
        "skipped_blurry": len(blurry_paths),
        "ranked": ranked,
    }

    output_json = json.dumps(output_data, indent=2)

    if args.output:
        Path(args.output).write_text(output_json)
        print(f"[ranker] results written to {args.output}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
