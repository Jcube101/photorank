"""
v1 API scoring paths, with scoring stubbed (no network, no images).

Set mode must never rank a set with a photo silently dropped, and nothing that
identifies or describes a photo may reach the logs or the error detail
(CLAUDE.md privacy rule 2). Burst mode never calls Gemini.
"""

import logging

import pytest
from fastapi import HTTPException

from api import main as api
from core.profiles import PROFILES

PHOTOS = [{"photo_id": f"IMG_48{i:02d}.jpg", "path": f"/tmp/upload-{i}.jpg"} for i in range(3)]


def _tech(path, photo_id=None):
    return {"photo_id": photo_id, "path": path, "sharpness": 7.0, "exposure": 6.0,
            "blur_raw": 300.0, "tenengrad_raw": 1.0}


def _gemini_rows(photo_ids):
    return [
        {"photo_id": pid, "subject_1_expression": 7, "subject_2_expression": None,
         "expression": 7.0, "camera_engagement": 8, "composition": 6, "subject_focus": 7,
         "relative_rank": rank, "notes": "ok"}
        for rank, pid in enumerate(photo_ids, 1)
    ]


@pytest.fixture
def stub_tech(monkeypatch):
    monkeypatch.setattr(api, "compute_technical_scores", _tech)


def _logged(caplog) -> str:
    return " ".join(r.getMessage() for r in caplog.records)


def test_set_mode_ranks_every_photo(monkeypatch, stub_tech):
    monkeypatch.setattr(api, "score_photos",
                        lambda paths, photo_ids, profile: _gemini_rows(photo_ids))
    out = api._run_set(PHOTOS, "family", PROFILES["family"])
    assert sorted(r["photo_id"] for r in out["ranked"]) == sorted(p["photo_id"] for p in PHOTOS)


def test_set_mode_fails_instead_of_dropping_a_photo(monkeypatch, stub_tech, caplog):
    monkeypatch.setattr(api, "score_photos",
                        lambda paths, photo_ids, profile: _gemini_rows(photo_ids[:-1]))
    with caplog.at_level(logging.INFO, logger="photorank.api"), pytest.raises(HTTPException) as err:
        api._run_set(PHOTOS, "family", PROFILES["family"])
    assert err.value.status_code == 502
    assert "1 of 3" in err.value.detail
    assert "/rank rejected: 502" in _logged(caplog)
    assert not any(p["photo_id"] in _logged(caplog) + err.value.detail for p in PHOTOS)


def test_gemini_failure_never_logs_or_returns_the_reply(monkeypatch, stub_tech, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError(
            "Gemini scoring failed after 3 attempts: Reply names the wrong photos\n"
            'Last response: [{"photo_id": "IMG_4800.jpg", "notes": "a child on a beach"}]'
        )

    monkeypatch.setattr(api, "score_photos", fail)
    with caplog.at_level(logging.INFO, logger="photorank.api"), pytest.raises(HTTPException) as err:
        api._run_set(PHOTOS, "family", PROFILES["family"])
    assert err.value.status_code == 502
    text = err.value.detail + " " + _logged(caplog)
    assert "IMG_4800" not in text and "beach" not in text and "Last response" not in text
    assert "3 photo(s)" in err.value.detail


def test_burst_mode_never_calls_gemini(monkeypatch):
    def no_gemini(*args, **kwargs):
        raise AssertionError("burst mode must not call Gemini")

    def burst(path, photo_id=None):
        return {"photo_id": photo_id, "path": path, "sharpness": 7.0, "exposure": 6.0,
                "face_sharpness": 8.0, "face_exposure": 6.5, "face_detected": True,
                "blur_raw": 300.0, "face_blur_raw": 400.0, "tenengrad_raw": 1.0}

    monkeypatch.setattr(api, "score_photos", no_gemini)
    monkeypatch.setattr(api, "compute_burst_scores", burst)
    out = api._run_burst(PHOTOS)
    assert out["mode"] == "burst" and len(out["ranked"]) == 3


# --- photo_id is the uploaded filename, unchanged ---------------------------
# The PWA finds each result's preview by photo_id. Rewriting the name (the old
# code lower-cased the extension and added ".jpg" when there was none) made
# every Android result show the placeholder. See LEARNINGS 2026-11-27.

def test_photo_ids_keep_each_name_exactly():
    names = ["1000330417.JPG", "1000330418", "IMG_1.jpeg", "photo.HEIC", "a.b.Jpg"]
    assert api._photo_ids(names) == names


def test_photo_ids_only_rename_repeats_and_missing_names():
    assert api._photo_ids(["a.jpg", "a.jpg", "a_1.jpg", "a.jpg"]) == [
        "a.jpg", "a_1.jpg", "a_1_1.jpg", "a_2.jpg",
    ]
    assert api._photo_ids(["1000330417", "1000330417"]) == ["1000330417", "1000330417_1"]
    assert api._photo_ids([None, ""]) == ["image.jpg", "image_1.jpg"]


def _jpeg(seed: int) -> bytes:
    import cv2
    import numpy as np

    noise = np.random.default_rng(seed).integers(0, 256, (240, 320, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", noise)
    assert ok
    return buf.tobytes()


@pytest.mark.parametrize("mode", ["burst", "set"])
def test_rank_returns_the_uploaded_names_as_photo_ids(monkeypatch, mode):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "score_photos",
                        lambda paths, photo_ids, profile: _gemini_rows(photo_ids))
    names = ["1000330417.JPG", "1000330418", "1000330419.jpg"]
    files = [("files", (n, _jpeg(i), "image/jpeg")) for i, n in enumerate(names)]
    res = TestClient(api.app).post("/rank", files=files, data={"mode": mode})
    assert res.status_code == 200, res.text
    assert sorted(r["photo_id"] for r in res.json()["ranked"]) == sorted(names)
