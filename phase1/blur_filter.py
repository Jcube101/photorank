"""
Deterministic technical quality scorer using OpenCV and MediaPipe.

Three metrics computed locally — no API cost, no network, deterministic:

  sharpness    Combined Laplacian variance + Tenengrad gradient energy → 1–10
               Calibrated for phone photos: ~100 lap = blurry, ~400 = sharp.
               Using both measures catches cases where one alone is fooled
               (e.g. Laplacian is misled by high-contrast edges in an OOF shot).

  exposure     Histogram analysis: mean brightness, contrast (std dev),
               highlight and shadow clipping fractions → 1–10.

  eye_openness MediaPipe Face Mesh eye aspect ratio (EAR) → 1–10.
               Returns None if no face detected or MediaPipe unavailable —
               ranker redistributes this axis's weight in that case.

Usage:
  from phase1.blur_filter import compute_technical_scores_batch, collect_images, filter_blurry
"""

import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    import mediapipe as mp
    _MP = mp.solutions.face_mesh
    _MEDIAPIPE_AVAILABLE = True
except ImportError:
    _MEDIAPIPE_AVAILABLE = False


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}

# 6-point EAR landmark indices for left and right eyes (MediaPipe Face Mesh)
# Order: [outer_corner, upper_inner, upper_outer, inner_corner, lower_inner, lower_outer]
_LEFT_EYE  = [33,  160, 158, 133, 153, 144]
_RIGHT_EYE = [362, 385, 387, 263, 373, 380]

# Resolution cap for MediaPipe — full-res is unnecessary and slows Pi significantly
_MP_MAX_DIM = 640


# ---------------------------------------------------------------------------
# Internal metric functions (operate on pre-loaded numpy arrays)
# ---------------------------------------------------------------------------

def _laplacian_var(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _tenengrad(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx ** 2 + gy ** 2))


def _ear(landmarks, indices: list[int], img_w: int, img_h: int) -> float:
    """Eye Aspect Ratio from 6 landmarks: (vert1 + vert2) / (2 * horiz)."""
    pts = [(landmarks[i].x * img_w, landmarks[i].y * img_h) for i in indices]
    v1 = math.dist(pts[1], pts[5])
    v2 = math.dist(pts[2], pts[4])
    h  = math.dist(pts[0], pts[3])
    return (v1 + v2) / (2.0 * h) if h > 0 else 0.0


# ---------------------------------------------------------------------------
# Normalised scoring functions (all return float in [1.0, 10.0])
# ---------------------------------------------------------------------------

def score_sharpness(gray: np.ndarray) -> float:
    """
    Combined Laplacian + Tenengrad → 1–10.

    Reference ceiling values (phone photos):
      Laplacian var ~400  → score ≈ 9.0
      Tenengrad mean ~2500 → score ≈ 9.0
    """
    lap = _laplacian_var(gray)
    ten = _tenengrad(gray)
    lap_score = 1.0 + 9.0 * math.log1p(lap) / math.log1p(400)
    ten_score = 1.0 + 9.0 * math.log1p(ten) / math.log1p(2500)
    return round(min(10.0, max(1.0, (lap_score + ten_score) / 2.0)), 2)


def score_exposure(gray: np.ndarray) -> float:
    """
    Histogram-based exposure quality → 1–10.

    Penalises deviation from neutral brightness (~125), low contrast,
    and clipped highlights / crushed shadows.
    """
    mean             = float(np.mean(gray))
    std              = float(np.std(gray))
    n                = gray.size
    highlight_clip   = float(np.sum(gray > 245)) / n
    shadow_clip      = float(np.sum(gray < 10))  / n

    # Brightness: ideal near 125; ±128 deviation → –7 points
    brightness_score = max(1.0, 10.0 - abs(mean - 125.0) / 125.0 * 7.0)

    # Contrast: std ~70 is ideal for most scenes; cap benefit beyond that
    contrast_score   = min(10.0, max(1.0, 1.0 + 9.0 * min(std, 70.0) / 70.0))

    # Clipping: each percentage point costs 0.4 score points
    clip_penalty = (highlight_clip + shadow_clip) * 40.0

    combined = brightness_score * 0.5 + contrast_score * 0.5 - clip_penalty
    return round(min(10.0, max(1.0, combined)), 2)


def score_eye_openness(bgr: np.ndarray) -> Optional[float]:
    """
    MediaPipe Face Mesh EAR → 1–10.

    Returns None when MediaPipe is not installed or no face is detected —
    callers must handle None gracefully.

    EAR calibration:
      ~0.10 (closed / blinking) → 1.0
      ~0.20 (half open)         → 5.5
      ~0.30 (fully open)        → 10.0
    Uses the worse (more-closed) eye so a single blink fails the shot.
    """
    if not _MEDIAPIPE_AVAILABLE:
        return None

    h, w = bgr.shape[:2]
    # Downscale for speed — face detection doesn't need full resolution
    if max(h, w) > _MP_MAX_DIM:
        scale  = _MP_MAX_DIM / max(h, w)
        small  = cv2.resize(bgr, (int(w * scale), int(h * scale)))
        sw, sh = small.shape[1], small.shape[0]
    else:
        small, sw, sh = bgr, w, h

    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    with _MP.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
    ) as face_mesh:
        result = face_mesh.process(rgb)

    if not result.multi_face_landmarks:
        return None

    lm        = result.multi_face_landmarks[0].landmark
    left_ear  = _ear(lm, _LEFT_EYE,  sw, sh)
    right_ear = _ear(lm, _RIGHT_EYE, sw, sh)
    worst_ear = min(left_ear, right_ear)

    score = 1.0 + 9.0 * (worst_ear - 0.10) / 0.20
    return round(min(10.0, max(1.0, score)), 2)


# ---------------------------------------------------------------------------
# Public batch API
# ---------------------------------------------------------------------------

def compute_technical_scores(image_path: str | Path) -> dict:
    """
    Compute all deterministic scores for a single image.

    Returns:
        {
            "photo_id":     str,   filename only
            "path":         str,   full path (needed for Gemini calls)
            "sharpness":    float, 1–10
            "exposure":     float, 1–10
            "eye_openness": float | None
            "blur_raw":     float  raw Laplacian variance (used for blur gate)
        }
    """
    p   = Path(image_path)
    bgr = cv2.imread(str(p))
    if bgr is None:
        raise ValueError(f"Could not read image: {p}")

    gray     = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur_raw = _laplacian_var(gray)

    return {
        "photo_id":     p.name,
        "path":         str(p),
        "sharpness":    score_sharpness(gray),
        "exposure":     score_exposure(gray),
        "eye_openness": score_eye_openness(bgr),
        "blur_raw":     round(blur_raw, 2),
    }


def compute_technical_scores_batch(
    image_paths: list[str | Path],
) -> list[dict]:
    """Compute deterministic scores for every image. Skips unreadable files."""
    results = []
    for p in image_paths:
        try:
            results.append(compute_technical_scores(p))
        except ValueError as e:
            print(f"  [technical] warning: {e}")
    return results


def filter_blurry(
    image_paths: list[str | Path],
    threshold: float = 100.0,
) -> tuple[list[str], list[str]]:
    """
    Split paths into (sharp, blurry) using raw Laplacian variance.
    Kept for standalone use; ranker.py uses compute_technical_scores_batch directly.
    """
    sharp, blurry = [], []
    for p in image_paths:
        path_str = str(p)
        try:
            bgr  = cv2.imread(path_str)
            if bgr is None:
                blurry.append(path_str)
                continue
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            (sharp if _laplacian_var(gray) >= threshold else blurry).append(path_str)
        except Exception as e:
            print(f"  [technical] warning reading {p}: {e}")
            blurry.append(path_str)
    return sharp, blurry


def collect_images(directory: str | Path) -> list[Path]:
    """Return all supported image files in a directory (non-recursive)."""
    d = Path(directory)
    if not d.is_dir():
        raise ValueError(f"Not a directory: {directory}")
    return sorted(p for p in d.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)


# Legacy alias
def score_blur(image_path: str | Path) -> float:
    """Raw Laplacian variance for a single image (higher = sharper)."""
    bgr = cv2.imread(str(image_path))
    if bgr is None:
        raise ValueError(f"Could not read image: {image_path}")
    return _laplacian_var(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python blur_filter.py <image_or_directory> [--threshold 100]")
        sys.exit(1)

    target    = Path(sys.argv[1])
    threshold = 100.0
    if "--threshold" in sys.argv:
        threshold = float(sys.argv[sys.argv.index("--threshold") + 1])

    paths   = collect_images(target) if target.is_dir() else [target]
    results = compute_technical_scores_batch(paths)

    for r in results:
        r["status"] = "sharp" if r["blur_raw"] >= threshold else "blurry"

    print(json.dumps(sorted(results, key=lambda x: -x["blur_raw"]), indent=2))
