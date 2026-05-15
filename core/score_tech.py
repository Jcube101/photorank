"""
Deterministic technical quality scorer using OpenCV.

Three metrics computed locally — no API cost, no network, always deterministic:

  sharpness    Combined Laplacian variance + Tenengrad gradient energy → 1–10
               Calibrated for ~1.5 MP images (post-ingest compression).
               Using both measures resists false positives — Laplacian alone
               is fooled by high-contrast edges in out-of-focus shots.

  exposure     Histogram analysis: mean brightness, contrast (std dev),
               highlight and shadow clipping fractions → 1–10.

  eye_openness Stubbed at 5.0 (neutral). MediaPipe is not available on
               Raspberry Pi ARM64. Replace this stub when an alternative
               blink/eye-openness method is found (dlib, InsightFace, or
               a lightweight OpenCV Haar cascade).
               rank.py treats any non-None value as a real score, so the
               stub participates in weighting normally — profiles that
               heavily weight eye_openness will be less useful until this
               is implemented.

blur_raw (raw Laplacian variance, unscaled) is also returned on every result
dict. rank.py uses it as the blur gate to exclude images before Gemini is called.

Usage:
  from core.score_tech import compute_technical_scores, compute_technical_scores_batch
"""

import math
from pathlib import Path

import cv2
import numpy as np


def _laplacian_var(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _tenengrad(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx ** 2 + gy ** 2))


# ---------------------------------------------------------------------------
# Normalised scoring functions — all return float in [1.0, 10.0]
# ---------------------------------------------------------------------------

def score_sharpness(gray: np.ndarray) -> float:
    """
    Combined Laplacian + Tenengrad → 1–10.

    Reference ceiling values (calibrated for ~1.5 MP phone photos):
      Laplacian var ~400   → score ≈ 9.0
      Tenengrad mean ~2500 → score ≈ 9.0
    """
    lap       = _laplacian_var(gray)
    ten       = _tenengrad(gray)
    lap_score = 1.0 + 9.0 * math.log1p(lap) / math.log1p(400)
    ten_score = 1.0 + 9.0 * math.log1p(ten) / math.log1p(2500)
    return round(min(10.0, max(1.0, (lap_score + ten_score) / 2.0)), 2)


def score_exposure(gray: np.ndarray) -> float:
    """
    Histogram-based exposure quality → 1–10.

    Penalises deviation from neutral brightness (~125), low contrast,
    and clipped highlights / crushed shadows.
    """
    mean           = float(np.mean(gray))
    std            = float(np.std(gray))
    n              = gray.size
    highlight_clip = float(np.sum(gray > 245)) / n
    shadow_clip    = float(np.sum(gray < 10))  / n

    brightness_score = max(1.0, 10.0 - abs(mean - 125.0) / 125.0 * 7.0)
    contrast_score   = min(10.0, max(1.0, 1.0 + 9.0 * min(std, 70.0) / 70.0))
    clip_penalty     = (highlight_clip + shadow_clip) * 40.0

    combined = brightness_score * 0.5 + contrast_score * 0.5 - clip_penalty
    return round(min(10.0, max(1.0, combined)), 2)


def score_eye_openness(_bgr: np.ndarray) -> float:
    """
    Stub — returns neutral 5.0 for all images.

    MediaPipe is not available on Raspberry Pi ARM64. Replace with a real
    implementation (dlib shape predictor, InsightFace, or OpenCV Haar cascade)
    once a Pi-compatible library is identified.

    Returning 5.0 rather than None keeps the eye_openness axis active in
    weighting (no weight redistribution) so output scores are comparable when
    the real implementation ships.
    """
    return 5.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_technical_scores(
    image_path: str | Path,
    photo_id: str | None = None,
) -> dict:
    """
    Compute all deterministic scores for a single image.

    Args:
        image_path: Path to the image file (typically the compressed path from ingest).
        photo_id:   Override for the photo identifier. When provided, this is used
                    instead of the filename so the original name survives compression.

    Returns:
        {
            "photo_id":     str,          — filename or override
            "path":         str,          — image_path as string
            "sharpness":    float,        — 1–10
            "exposure":     float,        — 1–10
            "eye_openness": float,        — 1–10 (5.0 stub until Pi-compatible detector ships)
            "blur_raw":     float,        — raw Laplacian variance (blur gate input)
        }
    """
    p   = Path(image_path)
    bgr = cv2.imread(str(p))
    if bgr is None:
        raise ValueError(f"Could not read image: {p}")

    gray     = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur_raw = _laplacian_var(gray)

    return {
        "photo_id":     photo_id if photo_id is not None else p.name,
        "path":         str(p),
        "sharpness":    score_sharpness(gray),
        "exposure":     score_exposure(gray),
        "eye_openness": score_eye_openness(bgr),
        "blur_raw":     round(blur_raw, 2),
    }


def compute_technical_scores_batch(
    image_paths: list[str | Path],
    photo_ids: list[str] | None = None,
) -> list[dict]:
    """
    Compute deterministic scores for a list of images.

    Args:
        image_paths: Paths to image files (compressed paths from ingest).
        photo_ids:   Optional list of original filenames, parallel to image_paths.
                     When provided, overrides the filename-derived photo_id.
    """
    results = []
    ids     = photo_ids or [None] * len(image_paths)
    for p, pid in zip(image_paths, ids):
        try:
            results.append(compute_technical_scores(p, photo_id=pid))
        except ValueError as e:
            print(f"  [score_tech] warning: {e}")
    return results


# ---------------------------------------------------------------------------
# CLI — standalone technical scoring without Gemini
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys
    from core.ingest import ingest, cleanup

    if len(sys.argv) < 2:
        print("Usage: python score_tech.py <directory_or_file> [--threshold 100]")
        sys.exit(1)

    threshold = 100.0
    if "--threshold" in sys.argv:
        threshold = float(sys.argv[sys.argv.index("--threshold") + 1])

    photos, tmp = ingest(sys.argv[1])
    try:
        results = compute_technical_scores_batch(
            [p["path"] for p in photos],
            photo_ids=[p["photo_id"] for p in photos],
        )
        for r in results:
            r["status"] = "sharp" if r["blur_raw"] >= threshold else "blurry"
        print(json.dumps(sorted(results, key=lambda x: -x["blur_raw"]), indent=2))
    finally:
        cleanup(tmp)
