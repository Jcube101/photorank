"""
CLI entry point for the PhotoRank scoring pipeline.

Two scoring modes:

  set mode (default, --mode set)
    Full two-layer pipeline for varied photo sets.
    1. ingest       — collect, validate, compress to ~1.5 MP
    2. score_tech   — deterministic scoring (sharpness, exposure, blur_raw)
    3. blur gate    — exclude images below blur_raw threshold
    4. score_vision — Gemini semantic scoring (expression, composition,
                      subject_focus, camera_engagement, relative_rank)
    5. merge        — combine layers by photo_id
    6. rank         — apply profile weights, sort by final_score
    7. output       — ranked JSON to stdout or --output file
    8. cleanup      — delete temp directory

  burst mode (--mode burst)
    Deterministic-only pipeline for small sets of near-identical burst shots.
    Skips Gemini entirely — no API cost, no network latency.
    1. ingest       — collect, validate, compress
    2. score_burst  — full-image + face-region sharpness and exposure
    3. blur gate    — exclude images below blur_raw threshold
    4. rank         — apply BURST_WEIGHTS
    5. output       — ranked JSON labelled as burst mode
    6. cleanup      — delete temp directory

Usage:
  python core/rank.py --profile family
  python core/rank.py --mode burst --input /path/to/burst_set
  python core/rank.py --input /path/to/photos --profile portrait --blur-threshold 80
  python core/rank.py --profile custom \\
      --weights '{"sharpness":0.2,"exposure":0.1,"expression":0.25,"composition":0.2,"subject_focus":0.25,"camera_engagement":0.05}'
  python core/rank.py --profile family --output output/results.json
"""

import argparse
import json
import sys
from pathlib import Path

from core.profiles import ALL_AXES, AXES_DETERMINISTIC, BURST_WEIGHTS, PROFILES, validate_weights

_BURST_MODE_MAX_PHOTOS = 6


# ---------------------------------------------------------------------------
# Burst mode helpers
# ---------------------------------------------------------------------------

def _rank_burst(burst_scores: list[dict]) -> list[dict]:
    """Apply BURST_WEIGHTS to burst scores. Returns list sorted by final_score desc."""
    axes = list(BURST_WEIGHTS.keys())

    source_label = {
        "face_sharpness": "deterministic (face crop)",
        "face_exposure":  "deterministic (face crop)",
        "sharpness":      "deterministic",
        "exposure":       "deterministic",
    }

    for s in burst_scores:
        s["final_score"] = round(
            sum(s[axis] * BURST_WEIGHTS[axis] for axis in axes), 3
        )
        s["score_breakdown"] = {
            axis: {
                "raw":          s[axis],
                "weight":       BURST_WEIGHTS[axis],
                "contribution": round(s[axis] * BURST_WEIGHTS[axis], 3),
                "source":       source_label.get(axis, "deterministic"),
            }
            for axis in axes
        }

    ranked = sorted(burst_scores, key=lambda x: -x["final_score"])
    for i, s in enumerate(ranked, 1):
        s["final_rank"] = i

    return ranked


def _print_burst_summary(ranked: list[dict], blurry_ids: list[str]) -> None:
    print("\n=== PhotoRank Results (burst mode) ===\n", file=sys.stderr)
    for photo in ranked:
        face_tag = "" if photo["face_detected"] else " [no face]"
        print(
            f"  #{photo['final_rank']:>2}  {photo['photo_id']:<40}"
            f"  score={photo['final_score']:.3f}"
            f"  face_sharp={photo['face_sharpness']:.2f}"
            f"  sharp={photo['sharpness']:.2f}{face_tag}",
            file=sys.stderr,
        )
    if blurry_ids:
        print(f"\n  Skipped (blurry): {len(blurry_ids)} photo(s)", file=sys.stderr)
        for name in blurry_ids:
            print(f"    {name}", file=sys.stderr)
    print(file=sys.stderr)


# ---------------------------------------------------------------------------
# Set mode helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="PhotoRank — deterministic + Gemini two-layer photo ranking"
    )
    parser.add_argument(
        "--mode", "-m",
        default="set",
        choices=["burst", "set"],
        help=(
            "Scoring mode. 'burst': deterministic only with face-region signals, "
            "no Gemini (fast, no API cost, for 2–6 near-identical shots). "
            "'set': full two-layer pipeline with Gemini semantic scoring (default)."
        ),
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
        help="Scoring profile — set mode only (default: family)",
    )
    parser.add_argument(
        "--weights", "-w",
        default=None,
        help=(
            "JSON weights for --profile custom (set mode only). "
            "All six axes required: sharpness, exposure, expression, "
            "composition, subject_focus, camera_engagement"
        ),
    )
    parser.add_argument(
        "--blur-threshold", "-b",
        type=float,
        default=100.0,
        help="Laplacian variance threshold — images below this are skipped (default: 100)",
    )
    parser.add_argument(
        "--no-blur-filter",
        action="store_true",
        help="Process all images regardless of sharpness",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Write JSON to file instead of stdout (e.g. output/results.json)",
    )
    args = parser.parse_args()

    # Weights only needed for set mode
    weights = None
    if args.mode == "set":
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

    print(f"[rank] ingesting images from {args.input}...", file=sys.stderr)
    try:
        photos, temp_dir = ingest(args.input)
    except ValueError as e:
        print(f"[rank] error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[rank] {len(photos)} image(s) loaded", file=sys.stderr)

    if args.mode == "burst" and len(photos) > _BURST_MODE_MAX_PHOTOS:
        print(
            f"[rank] warning: burst mode is designed for {_BURST_MODE_MAX_PHOTOS} or fewer "
            f"photos ({len(photos)} provided). Consider --mode set for larger sets.",
            file=sys.stderr,
        )

    try:
        # ------------------------------------------------------------------
        # BURST MODE — deterministic only, face-region signals
        # ------------------------------------------------------------------
        if args.mode == "burst":
            from core.score_burst import compute_burst_scores

            print("[rank] burst mode — computing face-region deterministic scores...", file=sys.stderr)
            all_scores: list[dict] = []
            for photo in photos:
                try:
                    score = compute_burst_scores(photo["path"], photo_id=photo["photo_id"])
                    all_scores.append(score)
                except ValueError as e:
                    print(f"  [rank] warning: {e}", file=sys.stderr)

            if not all_scores:
                print("[rank] no images could be scored.", file=sys.stderr)
                sys.exit(1)

            blur_gate_bypassed = False
            blurry_ids: list[str] = []
            if args.no_blur_filter:
                sharp_scores = all_scores
            else:
                sharp_scores  = [s for s in all_scores if s["blur_raw"] >= args.blur_threshold]
                blurry_scores = [s for s in all_scores if s["blur_raw"] <  args.blur_threshold]
                blurry_ids    = [s["photo_id"] for s in blurry_scores]
                print(
                    f"[rank] {len(sharp_scores)} sharp, {len(blurry_scores)} blurry "
                    f"(threshold={args.blur_threshold})",
                    file=sys.stderr,
                )
                # The blur gate thins a set; it must not refuse to rank. If nothing
                # clears the threshold, rank everything and pick the least-blurry.
                if not sharp_scores:
                    print(
                        f"[rank] all {len(all_scores)} image(s) below blur threshold "
                        f"({args.blur_threshold}); ranking all anyway (gate bypassed).",
                        file=sys.stderr,
                    )
                    sharp_scores       = all_scores
                    blurry_ids         = []
                    blur_gate_bypassed = True

            ranked = _rank_burst(sharp_scores)
            _print_burst_summary(ranked, blurry_ids)

            output_data = {
                "mode":               "burst",
                "burst_weights":      BURST_WEIGHTS,
                "blur_threshold":     None if args.no_blur_filter else args.blur_threshold,
                "blur_gate_bypassed": blur_gate_bypassed,
                "total_photos":       len(photos),
                "scored_photos":      len(sharp_scores),
                "skipped_blurry":     len(blurry_ids),
                "ranked":             ranked,
            }

        # ------------------------------------------------------------------
        # SET MODE — full two-layer pipeline with Gemini
        # ------------------------------------------------------------------
        else:
            from core.score_tech import compute_technical_scores
            from core.score_vision import score_photos

            print("[rank] computing technical scores (sharpness / exposure)...", file=sys.stderr)
            all_technical: list[dict] = []
            for photo in photos:
                try:
                    tech = compute_technical_scores(photo["path"], photo_id=photo["photo_id"])
                    all_technical.append(tech)
                except ValueError as e:
                    print(f"  [rank] warning: {e}", file=sys.stderr)

            if not all_technical:
                print("[rank] no images could be scored.", file=sys.stderr)
                sys.exit(1)

            blur_gate_bypassed = False
            blurry_ids = []
            if args.no_blur_filter:
                sharp_technical = all_technical
            else:
                sharp_technical  = [t for t in all_technical if t["blur_raw"] >= args.blur_threshold]
                blurry_technical = [t for t in all_technical if t["blur_raw"] <  args.blur_threshold]
                blurry_ids       = [t["photo_id"] for t in blurry_technical]
                print(
                    f"[rank] {len(sharp_technical)} sharp, {len(blurry_technical)} blurry "
                    f"(threshold={args.blur_threshold})",
                    file=sys.stderr,
                )
                # Gate thins the set before Gemini; it must not refuse the whole
                # batch. If nothing clears the threshold, score everything.
                if not sharp_technical:
                    print(
                        f"[rank] all {len(all_technical)} image(s) below blur threshold "
                        f"({args.blur_threshold}); scoring all anyway (gate bypassed).",
                        file=sys.stderr,
                    )
                    sharp_technical    = all_technical
                    blurry_ids         = []
                    blur_gate_bypassed = True

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

            gemini_by_id = {s["photo_id"]: s for s in gemini_scores}
            merged: list[dict] = []
            for tech in sharp_technical:
                pid = tech["photo_id"]
                if pid not in gemini_by_id:
                    print(f"[rank] warning: no Gemini score for {pid}, skipping", file=sys.stderr)
                    continue
                merged.append(_merge(tech, gemini_by_id[pid]))

            ranked = rank_photos(merged, weights)
            _print_summary(ranked, blurry_ids)

            output_data = {
                "mode":               "set",
                "profile":            args.profile,
                "weights":            weights,
                "blur_threshold":     None if args.no_blur_filter else args.blur_threshold,
                "blur_gate_bypassed": blur_gate_bypassed,
                "total_photos":       len(photos),
                "scored_photos":      len(merged),
                "skipped_blurry":     len(blurry_ids),
                "ranked":             ranked,
            }

        # ------------------------------------------------------------------
        # Output
        # ------------------------------------------------------------------
        output_json = json.dumps(output_data, indent=2)

        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(output_json)
            print(f"[rank] results written to {args.output}", file=sys.stderr)
        else:
            print(output_json)

    finally:
        cleanup(temp_dir)


if __name__ == "__main__":
    main()
