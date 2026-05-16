"""
Face-region deterministic scorer for burst mode.

Burst mode skips Gemini entirely and scores using only deterministic signals,
adding two face-region-specific measures to the existing full-image scores:

  face_sharpness  Laplacian variance on the detected face bounding box crop.
                  Full-image sharpness misses per-face focus differences that
                  matter most in burst shots — one frame has the face sharp,
                  another has it slightly soft.

  face_exposure   Exposure score on the face crop. Face lighting can differ
                  within a burst even when the overall frame is identically
                  exposed (e.g. a slight head turn changes how light falls).

Face detection uses the OpenCV frontal-face Haar cascade — Pi-compatible,
zero additional dependencies beyond the existing opencv-python-headless.

When no face is detected, face_sharpness and face_exposure fall back to the
full-image values so the photo still receives a useful score.

Usage:
  from core.score_burst import compute_burst_scores
  result = compute_burst_scores(path, photo_id="IMG_4821.jpg")
"""

import math
from pathlib import Path

import cv2
import numpy as np

from core.score_tech import score_exposure, score_sharpness

_FACE_CASCADE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# Calibrated for face-crop Laplacian at ~1.5 MP source images after compression.
# Face crops are smaller regions — raw variance can range similarly to full image.
# Using the same ceiling as full-image so scores are comparable across the two axes.
_FACE_LAP_CEILING = 2000


def _detect_largest_face(gray: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return the largest face bounding box (x, y, w, h) or None if not found."""
    faces = _FACE_CASCADE.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=5,
        minSize=(30, 30),
    )
    if len(faces) == 0:
        return None
    return tuple(map(int, max(faces, key=lambda f: f[2] * f[3])))


def _score_face_sharpness(face_gray: np.ndarray) -> tuple[float, float]:
    """Laplacian sharpness on a face crop → (score 1–10, raw variance)."""
    lap   = float(cv2.Laplacian(face_gray, cv2.CV_64F).var())
    score = round(
        min(10.0, max(1.0, 1.0 + 9.0 * math.log1p(lap) / math.log1p(_FACE_LAP_CEILING))),
        2,
    )
    return score, round(lap, 2)


def compute_burst_scores(
    image_path: str | Path,
    photo_id: str | None = None,
) -> dict:
    """
    Full deterministic scores for burst mode — full-image plus face-region.

    Args:
        image_path: Path to the (compressed) image file.
        photo_id:   Override photo identifier (use original filename, not compressed).

    Returns:
        {
            "photo_id":       str,   — filename or override
            "path":           str,
            "sharpness":      float, — 1–10, full image
            "exposure":       float, — 1–10, full image
            "blur_raw":       float, — raw Laplacian variance, full image
            "tenengrad_raw":  float, — raw Tenengrad mean, full image
            "face_detected":  bool,
            "face_sharpness": float, — 1–10; falls back to sharpness if no face
            "face_exposure":  float, — 1–10; falls back to exposure if no face
            "face_blur_raw":  float, — raw face-crop Laplacian; 0.0 if no face
        }
    """
    p   = Path(image_path)
    bgr = cv2.imread(str(p))
    if bgr is None:
        raise ValueError(f"Could not read image: {p}")

    gray                          = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    sharpness, blur_raw, ten_raw  = score_sharpness(gray)
    exposure                      = score_exposure(gray)
    face                          = _detect_largest_face(gray)

    if face is not None:
        x, y, w, h    = face
        face_gray      = gray[y : y + h, x : x + w]
        face_sharpness, face_blur_raw = _score_face_sharpness(face_gray)
        face_exposure  = score_exposure(face_gray)
        face_detected  = True
    else:
        face_sharpness = sharpness
        face_exposure  = exposure
        face_blur_raw  = 0.0
        face_detected  = False

    return {
        "photo_id":       photo_id if photo_id is not None else p.name,
        "path":           str(p),
        "sharpness":      sharpness,
        "exposure":       exposure,
        "blur_raw":       blur_raw,
        "tenengrad_raw":  ten_raw,
        "face_detected":  face_detected,
        "face_sharpness": face_sharpness,
        "face_exposure":  face_exposure,
        "face_blur_raw":  face_blur_raw,
    }


def compute_burst_scores_batch(
    image_paths: list[str | Path],
    photo_ids: list[str] | None = None,
) -> list[dict]:
    """
    Compute burst scores for a list of images.

    Args:
        image_paths: Paths to image files (compressed paths from ingest).
        photo_ids:   Optional list of original filenames, parallel to image_paths.
    """
    results = []
    ids     = photo_ids or [None] * len(image_paths)
    for p, pid in zip(image_paths, ids):
        try:
            results.append(compute_burst_scores(p, photo_id=pid))
        except ValueError as e:
            print(f"  [score_burst] warning: {e}")
    return results
