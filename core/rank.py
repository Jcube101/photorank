"""
CLI entry point for the PhotoRank scoring pipeline.

Pipeline:
  1. ingest       — collect images from source, validate, compress to ~1.5 MP
  2. score_tech   — deterministic scoring (sharpness, exposure, blur_raw, tenengrad_raw)
  3. blur gate    — exclude images below blur_raw threshold before Gemini is called
  4. score_vision — Gemini semantic scoring (expression, composition, subject_focus,
                    relative_rank)
  5. merge        — combine layers by photo_id
  6. rank         — apply profile weights, sort by final_score desc with
                    relative_rank as tiebreaker
  7. output       — ranked JSON to stdout or --output file
  8. cleanup      — delete temp directory (satisfies photo-deletion privacy requirement)

Usage:
  python core/rank.py --profile family
  python core/rank.py --input /path/to/photos --profile portrait --blur-threshold 80
  python core/rank.py --profile custom \\
      --weights '{"sharpness":0.2,"exposure":0.1,"expression":0.25,"composition":0.2,"subject_focus":0.25}'
  python core/rank.py --profile family --output output/results.json
"""

import argparse
import json
import sys
from pathlib import Path

from core.profiles import ALL_AXES, AXES_DETERMINISTIC, PROFILES, validate_weights


def _merge(technical: dict, gemini: dict) -> dict:
    """Combine deterministic and Gemini score dicts for one photo."""
    return {
        "photo_id":             technical["photo_id"],
        "sharpness":            technical["sharpness"],
        "exposure":             technical["exposure"],
        "subject_1_expression": gemini["subject_1_expression"],
        "subject_2_expression": gemini["subject_2_expression"],
        "expression":           gemini["expression"],
        "camera_engagement":    gemini["camera_engagement"],
        "composition":          gemini["composition"],
        "subject_focus":        gemini["subject_focus"],
        "relative_rank":        gemini["relative_rank"],
        "notes":                gemini["notes"],
    }


def rank_photos(
    merged_scores: list[dict],
    weights: dict[str, float],
) -> list[dict]:
    """
    Apply profile weights to merged scores. Returns list sorted by final_score desc,
    with relative_rank (from Gemini) as a tiebreaker.
    """
    validate_weights(weights)

    for s in merged_scores:
        s["final_score"] = round(
            sum(s[axis] * weights[axis] for axis in ALL_AXES), 3
        )
        s["score_breakdown"] = {
            axis: {
                "raw":              s[axis],
                "weight":           weights[axis],
                "effective_weight": weights[axis],
                "contribution":     round(s[axis] * weights[axis], 3),
                "source":           "deterministic" if axis in AXES_DETERMINISTIC else "gemini",
            }
            for axis in ALL_AXES
        }

    ranked = sorted(
        merged_scores,
        key=lambda x: (-x["final_score"], x.get("relative_rank", 999)),
    )
    for i, s in enumerate(ranked, 1):
        s["final_rank"] = i

    return ranked


def _print_summary(ranked: list[dict], blurry_ids: list[str]) -> None:
    print("\n=== PhotoRank Results ===\n", file=sys.stderr)
    for photo in ranked:
        print(
            f"  #{photo['final_rank']:>2}  {photo['photo_id']:<40}"
            f"  score={photo['final_score']:.3f}  — {photo['notes']}",
            file=sys.stderr,
        )
    if blurry_ids:
        print(f"\n  Skipped (blurry): {len(blurry_ids)} photo(s)", file=sys.stderr)
        for name in blurry_ids:
            print(f"    {name}", file=sys.stderr)
    print(file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PhotoRank — deterministic + Gemini two-layer photo ranking"
    )
    parser.add_argument(
        "--input", "-i",
        default="input",
        help="Directory of photos to rank (default: input/)",
    )
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
            "JSON weights for --profile custom. All five axes required: "
            "sharpness, exposure, expression, composition, subject_focus"
        ),
    )
    parser.add_argument(
        "--blur-threshold", "-b",
        type=float,
        default=100.0,
        help="Laplacian variance threshold — images below this skip Gemini (default: 100)",
    )
    parser.add_argument(
        "--no-blur-filter",
        action="store_true",
        help="Send all images to Gemini regardless of sharpness",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Write JSON to file instead of stdout (e.g. output/results.json)",
    )
    args = parser.parse_args()

    # Resolve weights before doing any work
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

    from core.ingest import cleanup, ingest
    from core.score_tech import compute_technical_scores
    from core.score_vision import score_photos

    # Step 1: ingest — collect, validate, compress
    print(f"[rank] ingesting images from {args.input}...", file=sys.stderr)
    try:
        photos, temp_dir = ingest(args.input)
    except ValueError as e:
        print(f"[rank] error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[rank] {len(photos)} image(s) loaded", file=sys.stderr)

    try:
        # Step 2: deterministic scoring — photo_id override keeps original filenames
        print("[rank] computing technical scores (sharpness / exposure)...", file=sys.stderr)
        all_technical: list[dict] = []
        for photo in photos:
            try:
                tech = compute_technical_scores(photo["path"], photo_id=photo["photo_id"])
                all_technical.append(tech)
            except ValueError as e:
                print(f"  [rank] warning: {e}", file=sys.stderr)

        # Step 3: blur gate
        if args.no_blur_filter:
            sharp_technical  = all_technical
            blurry_ids: list[str] = []
        else:
            sharp_technical  = [t for t in all_technical if t["blur_raw"] >= args.blur_threshold]
            blurry_technical = [t for t in all_technical if t["blur_raw"] <  args.blur_threshold]
            blurry_ids       = [t["photo_id"] for t in blurry_technical]
            print(
                f"[rank] {len(sharp_technical)} sharp, {len(blurry_technical)} blurry "
                f"(threshold={args.blur_threshold})",
                file=sys.stderr,
            )

        if not sharp_technical:
            print(
                "[rank] all images are below the blur threshold. "
                "Lower --blur-threshold or use --no-blur-filter.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Step 4: Gemini semantic scoring — pass original photo_ids as labels
        print(
            f"[rank] scoring {len(sharp_technical)} photo(s) via Gemini "
            f"(profile={args.profile})...",
            file=sys.stderr,
        )
        gemini_scores = score_photos(
            [t["path"] for t in sharp_technical],
            photo_ids=[t["photo_id"] for t in sharp_technical],
            profile=args.profile,
        )

        # Step 5: merge layers by photo_id
        gemini_by_id = {s["photo_id"]: s for s in gemini_scores}
        merged: list[dict] = []
        for tech in sharp_technical:
            pid = tech["photo_id"]
            if pid not in gemini_by_id:
                print(f"[rank] warning: no Gemini score for {pid}, skipping", file=sys.stderr)
                continue
            merged.append(_merge(tech, gemini_by_id[pid]))

        # Step 6: rank
        ranked = rank_photos(merged, weights)

        _print_summary(ranked, blurry_ids)

        output_data = {
            "profile":        args.profile,
            "weights":        weights,
            "blur_threshold": None if args.no_blur_filter else args.blur_threshold,
            "total_photos":   len(photos),
            "scored_photos":  len(merged),
            "skipped_blurry": len(blurry_ids),
            "ranked":         ranked,
        }
        output_json = json.dumps(output_data, indent=2)

        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output_json)
            print(f"[rank] results written to {args.output}", file=sys.stderr)
        else:
            print(output_json)

    finally:
        # Step 7: always delete compressed temp files (privacy requirement)
        cleanup(temp_dir)


if __name__ == "__main__":
    main()
