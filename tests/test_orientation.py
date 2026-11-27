"""
EXIF orientation for photos uploaded with their metadata stripped.

When the PWA can't compress a photo, it uploads the original JPEG with every
metadata segment removed (frontend/src/lib/stripJpeg.js), which drops the
orientation tag too, so a photo the camera stored sideways arrives sideways.
The PWA sends that one value in the `orientations` field, and ingest turns the
pixels upright before scoring: face detection only finds upright faces.
"""

import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import main as api
from tests.exif_jpeg import jpeg

STORED = (240, 320)  # (height, width) of the pixels as the camera stored them
NAMES = ["1000330417.jpg", "1000330418.jpg"]


def _decode(data: bytes, *, ignore_orientation: bool) -> np.ndarray:
    flags = cv2.IMREAD_COLOR | (cv2.IMREAD_IGNORE_ORIENTATION if ignore_orientation else 0)
    img = cv2.imdecode(np.frombuffer(data, np.uint8), flags)
    assert img is not None
    return img


def _correlation(a: np.ndarray, b: np.ndarray) -> float:
    a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(float).ravel()
    b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(float).ravel()
    return float(np.corrcoef(a, b)[0, 1])


@pytest.mark.parametrize("orientation", range(1, 9))
def test_apply_orientation_turns_pixels_as_exif_says(orientation):
    from core.ingest import apply_orientation

    # OpenCV applies a file's own EXIF orientation when decoding: the reference.
    data = jpeg(seed=orientation, orientation=orientation, blur=1.0)
    stored = _decode(data, ignore_orientation=True)
    assert stored.shape[:2] == STORED
    upright = _decode(data, ignore_orientation=False)
    assert np.array_equal(apply_orientation(stored, orientation), upright)


def test_sent_orientations_keep_only_values_1_to_8():
    raw = json.dumps({"a.jpg": 6, "b.jpg": 8, "c.jpg": 9, "d.jpg": 0, "e.jpg": "6",
                      "f.jpg": True, "g.jpg": 6.0, "h.jpg": None})
    assert api._sent_orientations(raw) == {"a.jpg": 6, "b.jpg": 8}
    for bad in (None, "", "not json", "[6]", "6"):
        assert api._sent_orientations(bad) == {}


def _scored(monkeypatch, files, data) -> list[np.ndarray]:
    """POST /rank (burst) and return the pixels each photo was scored on."""
    seen = []
    real = api.compute_burst_scores

    def spy(path, photo_id=None):
        seen.append(cv2.imread(str(path)))
        return real(path, photo_id=photo_id)

    monkeypatch.setattr(api, "compute_burst_scores", spy)
    res = TestClient(api.app).post("/rank", files=files, data={"mode": "burst", **data})
    assert res.status_code == 200, res.text
    return seen


def _upload(photos: list[bytes]) -> list:
    return [("files", (n, p, "image/jpeg")) for n, p in zip(NAMES, photos)]


def test_a_sideways_stripped_original_is_scored_upright(monkeypatch):
    # A stripped original: the camera's sideways pixels, and no EXIF at all.
    photos = [jpeg(seed=i, exif=False, blur=1.0) for i in range(2)]
    sent = {"orientations": json.dumps({n: 6 for n in NAMES})}
    scored = _scored(monkeypatch, _upload(photos), sent)

    for got, data in zip(scored, photos):
        upright = cv2.rotate(_decode(data, ignore_orientation=True), cv2.ROTATE_90_CLOCKWISE)
        assert got.shape[:2] == (STORED[1], STORED[0])  # portrait, as taken
        assert _correlation(got, upright) > 0.9


def test_a_sent_orientation_is_applied_once_even_if_the_file_still_has_one(monkeypatch):
    photos = [jpeg(seed=i, orientation=6, blur=1.0) for i in range(2)]
    sent = {"orientations": json.dumps({n: 6 for n in NAMES})}
    for got, data in zip(_scored(monkeypatch, _upload(photos), sent), photos):
        assert _correlation(got, _decode(data, ignore_orientation=False)) > 0.9


@pytest.mark.parametrize("field", [None, "not json", json.dumps({n: "6" for n in NAMES}),
                                   json.dumps({n: 9 for n in NAMES}), json.dumps({"other.jpg": 6})],
                         ids=["absent", "not_json", "string", "out_of_range", "other_name"])
def test_without_a_usable_orientation_nothing_is_turned(monkeypatch, field):
    photos = [jpeg(seed=i, exif=False, blur=1.0) for i in range(2)]
    data = {} if field is None else {"orientations": field}
    for got, sent in zip(_scored(monkeypatch, _upload(photos), data), photos):
        assert got.shape[:2] == STORED
        assert _correlation(got, _decode(sent, ignore_orientation=True)) > 0.9


def test_a_file_s_own_orientation_still_applies_without_the_field(monkeypatch):
    # Uploads of untouched originals (curl, scripts) keep working as before.
    photos = [jpeg(seed=i, orientation=8, blur=1.0) for i in range(2)]
    for got, data in zip(_scored(monkeypatch, _upload(photos), {}), photos):
        assert _correlation(got, _decode(data, ignore_orientation=False)) > 0.9


def test_orientations_never_reach_the_logs(monkeypatch, caplog):
    import logging

    photos = [jpeg(seed=i, exif=False, blur=1.0) for i in range(2)]
    field = json.dumps({n: 6 for n in NAMES})
    with caplog.at_level(logging.DEBUG):
        _scored(monkeypatch, _upload(photos), {"orientations": field})
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "orientation" not in logged.lower()
    assert all(n not in logged for n in NAMES)
