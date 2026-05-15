"""
Deterministic technical quality scorer using OpenCV.

Two metrics computed locally — no API cost, no network, always deterministic:

  sharpness    Combined Laplacian variance + Tenengrad gradient energy → 1–10
               Calibrated for ~1.5 MP images (post-ingest compression).
               Using both measures resists false positives — Laplacian alone
               is fooled by high-contrast edges in out-of-focus shots.

  exposure     Histogram analysis: mean brightness, contrast (std dev),
               highlight and shadow clipping fractions → 1–10.

blur_raw (raw Laplacian variance, unscaled) and tenengrad_raw are also returned
on every result dict. rank.py uses blur_raw as the blur gate to exclude images
before Gemini is called. tenengrad_raw is logged for diagnostic visibility.

eye_openness is not implemented. MediaPipe is unavailable on Raspberry Pi ARM64.
See LEARNINGS.md for candidate alternatives (dlib, InsightFace, OpenCV Haar
cascade). eye_openness has been removed from all scoring profiles until a
Pi-compatible implementation is found.

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

def score_sharpness(gray: np.ndarray) -> tuple[float, float, float]:
    """
    Combined Laplacian + Tenengrad → 1–10, plus raw values.

    Ceiling values calibrated for sharp phone photos at ~1.5 MP:
      Laplacian var  ~2000  → score ≈ 9.0
      Tenengrad mean ~12000 → score ≈ 9.0

    Returns (sharpness_score, lap_raw, ten_raw).
    """
    lap       = _laplacian_var(gray)
    ten       = _tenengrad(gray)
    lap_score = 1.0 + 9.0 * math.log1p(lap) / math.log1p(2000)
    ten_score = 1.0 + 9.0 * math.log1p(ten) / math.log1p(12000)
    score     = round(min(10.0, max(1.0, (lap_score + ten_score) / 2.0)), 2)
    return score, round(lap, 2), round(ten, 2)


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
            "photo_id":      str,   — filename or override
            "path":          str,   — image_path as string
            "sharpness":     float, — 1–10
            "exposure":      float, — 1–10
            "blur_raw":      float, — raw Laplacian variance (blur gate input)
            "tenengrad_raw": float, — raw Tenengrad mean (diagnostic)
        }
    """
    p   = Path(image_path)
    bgr = cv2.imread(str(p))
    if bgr is None:
        raise ValueError(f"Could not read image: {p}")

    gray                          = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    sharpness, blur_raw, ten_raw  = score_sharpness(gray)

    return {
        "photo_id":      photo_id if photo_id is not None else p.name,
        "path":          str(p),
        "sharpness":     sharpness,
        "exposure":      score_exposure(gray),
        "blur_raw":      blur_raw,
        "tenengrad_raw": ten_raw,
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
