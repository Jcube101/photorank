"""
Blur detection using OpenCV Laplacian variance.

Higher variance = sharper image. Typical thresholds:
  < 50   : obviously blurry, reject
  50-100 : soft, flag for review
  > 100  : acceptably sharp

Usage:
  from phase1.blur_filter import score_blur, filter_blurry
"""

import cv2
import os
from pathlib import Path


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}


def score_blur(image_path: str | Path) -> float:
    """Return Laplacian variance for a single image. Higher = sharper."""
    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def score_blur_batch(image_paths: list[str | Path]) -> dict[str, float]:
    """Return {path_str: blur_score} for each image."""
    results = {}
    for p in image_paths:
        try:
            results[str(p)] = score_blur(p)
        except ValueError as e:
            print(f"  [blur] warning: {e}")
            results[str(p)] = 0.0
    return results


def filter_blurry(
    image_paths: list[str | Path],
    threshold: float = 100.0,
) -> tuple[list[str], list[str]]:
    """
    Split images into (sharp, blurry) lists based on threshold.
    Returns lists of path strings.
    """
    scores = score_blur_batch(image_paths)
    sharp = [p for p, s in scores.items() if s >= threshold]
    blurry = [p for p, s in scores.items() if s < threshold]
    return sharp, blurry


def collect_images(directory: str | Path) -> list[Path]:
    """Return all supported image files in a directory (non-recursive)."""
    d = Path(directory)
    if not d.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    return sorted(
        p for p in d.iterdir()
        if p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python blur_filter.py <image_or_directory> [--threshold 100]")
        sys.exit(1)

    target = Path(sys.argv[1])
    threshold = 100.0
    if "--threshold" in sys.argv:
        idx = sys.argv.index("--threshold")
        threshold = float(sys.argv[idx + 1])

    if target.is_dir():
        paths = collect_images(target)
    else:
        paths = [target]

    scores = score_blur_batch(paths)
    output = [
        {
            "path": p,
            "blur_score": round(s, 2),
            "status": "sharp" if s >= threshold else "blurry",
        }
        for p, s in sorted(scores.items(), key=lambda x: -x[1])
    ]
    print(json.dumps(output, indent=2))
