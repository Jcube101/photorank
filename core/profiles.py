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


def validate_weights(weights: dict[str, float]) -> None:
    """Raise ValueError if weights are missing axes or don't sum to 1.0 (±0.001)."""
    missing = set(ALL_AXES) - set(weights.keys())
    if missing:
        raise ValueError(f"Weights missing axes: {missing}")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")
