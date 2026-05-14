"""
Deterministic technical quality scorer using OpenCV and MediaPipe.

Three metrics computed locally — no API cost, no network, always deterministic:

  sharpness    Combined Laplacian variance + Tenengrad gradient energy → 1–10
               Calibrated for ~1.5 MP images (post-ingest compression).
               Using both measures resists false positives — Laplacian alone
               is fooled by high-contrast edges in out-of-focus shots.

  exposure     Histogram analysis: mean brightness, contrast (std dev),
               highlight and shadow clipping fractions → 1–10.

  eye_openness MediaPipe Face Mesh EAR (eye aspect ratio) → 1–10.
               Returns None if no face detected or MediaPipe not installed.
               rank.py redistributes this axis's weight when None.

blur_raw (raw Laplacian variance, unscaled) is also returned on every result
dict. rank.py uses it as the blur gate to exclude images before Gemini is called.

Usage:
  from core.score_tech import compute_technical_scores, compute_technical_scores_batch
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


# 6-point EAR landmark indices (MediaPipe Face Mesh)
# Order per eye: [outer_corner, upper_a, upper_b, inner_corner, lower_a, lower_b]
_LEFT_EYE  = [33,  160, 158, 133, 153, 144]
_RIGHT_EYE = [362, 385, 387, 263, 373, 380]

_MP_MAX_DIM = 640  # downscale cap for MediaPipe — sufficient for face detection


# ---------------------------------------------------------------------------
# Internal signal functions (operate on pre-loaded numpy arrays)
# ---------------------------------------------------------------------------

def _laplacian_var(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _tenengrad(gray: np.ndarray) -> float:
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx ** 2 + gy ** 2))


def _ear(landmarks, indices: list[int], img_w: int, img_h: int) -> float:
    """Eye Aspect Ratio: (vertical_a + vertical_b) / (2 × horizontal)."""
    pts = [(landmarks[i].x * img_w, landmarks[i].y * img_h) for i in indices]
    v1  = math.dist(pts[1], pts[5])
    v2  = math.dist(pts[2], pts[4])
    h   = math.dist(pts[0], pts[3])
    return (v1 + v2) / (2.0 * h) if h > 0 else 0.0


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


def score_eye_openness(bgr: np.ndarray) -> Optional[float]:
    """
    MediaPipe Face Mesh EAR → 1–10.

    Returns None when MediaPipe is not installed or no face is detected.
    Scores the worse (more-closed) eye so a single blink fails the shot.

    EAR calibration:
      ~0.10 (closed / blinking) → 1.0
      ~0.20 (half open)         → 5.5
      ~0.30 (fully open)        → 10.0
    """
    if not _MEDIAPIPE_AVAILABLE:
        return None

    h, w = bgr.shape[:2]
    if max(h, w) > _MP_MAX_DIM:
        scale      = _MP_MAX_DIM / max(h, w)
        small      = cv2.resize(bgr, (int(w * scale), int(h * scale)))
        sw, sh     = small.shape[1], small.shape[0]
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
            "eye_openness": float | None, — 1–10 or None
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
