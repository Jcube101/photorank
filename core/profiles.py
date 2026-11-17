"""
Single source of truth for scoring profiles, axis definitions, and weight validation.
No profile weights are defined anywhere else in the codebase.
"""

AXES_DETERMINISTIC = ["sharpness", "exposure"]
AXES_SEMANTIC      = ["expression", "composition", "subject_focus", "camera_engagement"]
ALL_AXES           = AXES_DETERMINISTIC + AXES_SEMANTIC

PROFILES: dict[str, dict[str, float]] = {
    "family": {
        "expression":        0.25,
        "camera_engagement": 0.20,
        "subject_focus":     0.20,
        "sharpness":         0.19,
        "composition":       0.10,
        "exposure":          0.06,
    },
    "portrait": {
        "sharpness":         0.27,
        "expression":        0.27,
        "subject_focus":     0.20,
        "exposure":          0.16,
        "composition":       0.10,
        "camera_engagement": 0.00,
    },
    "event": {
        "composition":       0.28,
        "subject_focus":     0.22,
        "expression":        0.17,
        "sharpness":         0.17,
        "exposure":          0.16,
        "camera_engagement": 0.00,
    },
    "travel": {
        "composition":       0.35,
        "subject_focus":     0.25,
        "sharpness":         0.20,
        "exposure":          0.15,
        "expression":        0.05,
        "camera_engagement": 0.00,
    },
}

# Burst mode uses a separate axis set — face-region signals are only available
# when running score_burst.py, not in the standard set-mode pipeline.
# These weights are not validated by validate_weights (different axis set).
BURST_WEIGHTS: dict[str, float] = {
    "face_sharpness": 0.50,
    "sharpness":      0.20,
    "face_exposure":  0.20,
    "exposure":       0.10,
}

# Full shoot mode (docs/V2-PLAN.md §6.3) scores every group with both signal
# families: the six set-mode axes plus the two face-crop axes. These are
# starting values, to be tuned against the answer key (scripts/eval_shoot.py).
SHOOT_AXES = [
    "face_sharpness", "expression", "subject_focus", "composition",
    "camera_engagement", "exposure", "sharpness", "face_exposure",
]

SHOOT_WEIGHTS: dict[str, float] = {
    "face_sharpness":    0.30,
    "expression":        0.30,
    "subject_focus":     0.10,
    "composition":       0.10,
    "camera_engagement": 0.10,
    "exposure":          0.05,
    "sharpness":         0.05,
    "face_exposure":     0.00,
}

SHOOT_AXIS_SOURCES: dict[str, str] = {
    "face_sharpness":    "deterministic (face crop)",
    "face_exposure":     "deterministic (face crop)",
    "sharpness":         "deterministic",
    "exposure":          "deterministic",
    "expression":        "gemini",
    "subject_focus":     "gemini",
    "composition":       "gemini",
    "camera_engagement": "gemini",
}


def validate_weights(weights: dict[str, float]) -> None:
    """Raise ValueError if weights are missing axes or don't sum to 1.0 (±0.001)."""
    missing = set(ALL_AXES) - set(weights.keys())
    if missing:
        raise ValueError(f"Weights missing axes: {missing}")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")


def validate_shoot_weights(weights: dict[str, float]) -> None:
    """Raise ValueError unless weights cover exactly SHOOT_AXES and sum to 1.0 (±0.001)."""
    missing = set(SHOOT_AXES) - set(weights.keys())
    if missing:
        raise ValueError(f"Shoot weights missing axes: {sorted(missing)}")
    unknown = set(weights.keys()) - set(SHOOT_AXES)
    if unknown:
        raise ValueError(f"Shoot weights have unknown axes: {sorted(unknown)}")
    negative = sorted(a for a, w in weights.items() if w < 0)
    if negative:
        raise ValueError(f"Shoot weights must not be negative: {negative}")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"Shoot weights must sum to 1.0, got {total:.4f}")


# Validate on load: a bad edit to SHOOT_WEIGHTS fails at import, not mid-run.
validate_shoot_weights(SHOOT_WEIGHTS)
