"""
Single source of truth for scoring profiles, axis definitions, and weight validation.
No profile weights are defined anywhere else in the codebase.
"""

AXES_DETERMINISTIC = ["sharpness", "exposure", "eye_openness"]
AXES_SEMANTIC      = ["expression", "composition", "subject_focus"]
ALL_AXES           = AXES_DETERMINISTIC + AXES_SEMANTIC

PROFILES: dict[str, dict[str, float]] = {
    "family": {
        "expression":    0.25,
        "eye_openness":  0.20,
        "subject_focus": 0.20,
        "sharpness":     0.15,
        "composition":   0.12,
        "exposure":      0.08,
    },
    "portrait": {
        "eye_openness":  0.25,
        "sharpness":     0.20,
        "expression":    0.20,
        "subject_focus": 0.15,
        "exposure":      0.12,
        "composition":   0.08,
    },
    "event": {
        "composition":   0.25,
        "subject_focus": 0.20,
        "expression":    0.15,
        "sharpness":     0.15,
        "exposure":      0.15,
        "eye_openness":  0.10,
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
