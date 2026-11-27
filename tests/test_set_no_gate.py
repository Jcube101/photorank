"""
Set mode has no blur gate: every readable photo goes to Gemini and is ranked.
Sharpness is already a weighted axis. The old gate dropped any photo with
blur_raw < 100, but a fixed absolute threshold does not transfer across
lighting, scene and image size (LEARNINGS 2026-11-27): an indoor selfie burst
had fine frames at 59, 69 and 99.
"""

import json
import sys

from fastapi.testclient import TestClient

from api import main as api
from tests.exif_jpeg import jpeg

NAMES = [f"1000330{n}.jpg" for n in range(417, 424)]
# Four sharp photos, then three frames scoring about 59, 69 and 99, as the
# selfie burst did: (seed, Gaussian sigma) -> blur_raw 57.5, 68.2 and 99.4.
SOFT = [(4, 1.397), (5, 1.358), (6, 1.254)]
PHOTOS = [jpeg(seed=i, blur=0.5) for i in range(4)] + [jpeg(seed=s, blur=b) for s, b in SOFT]


def _gemini_rows(photo_ids):
    return [{"photo_id": pid, "subject_1_expression": 7, "subject_2_expression": None,
             "expression": 7.0, "camera_engagement": 7, "composition": 7, "subject_focus": 7,
             "relative_rank": i + 1, "notes": "ok"} for i, pid in enumerate(photo_ids)]


def test_a_7_photo_set_with_frames_at_59_69_and_99_ranks_all_7(monkeypatch):
    sent = []

    def gemini(paths, photo_ids, profile):
        sent.extend(photo_ids)
        return _gemini_rows(photo_ids)

    blur_raw, real = {}, api.compute_technical_scores

    def tech(path, photo_id=None):  # records what the old gate compared with 100
        scores = real(path, photo_id=photo_id)
        blur_raw[photo_id] = scores["blur_raw"]
        return scores

    monkeypatch.setattr(api, "score_photos", gemini)
    monkeypatch.setattr(api, "compute_technical_scores", tech)
    files = [("files", (n, p, "image/jpeg")) for n, p in zip(NAMES, PHOTOS)]
    res = TestClient(api.app).post("/rank", files=files, data={"mode": "set"})
    assert res.status_code == 200, res.text
    out = res.json()

    assert sorted(sent) == sorted(NAMES)  # every photo went to Gemini
    assert sorted(p["photo_id"] for p in out["ranked"]) == sorted(NAMES)
    assert out["left_out"] == []
    assert sorted(round(blur_raw[n]) for n in NAMES[4:]) == [58, 68, 99]  # all below the old gate
    for field in ("blur_threshold", "blur_gate_bypassed", "skipped_blurry"):
        assert field not in out


def test_the_cli_set_mode_also_ranks_all_7(tmp_path, monkeypatch):
    import core.rank
    import core.score_vision

    for name, data in zip(NAMES, PHOTOS):
        (tmp_path / name).write_bytes(data)
    out_file = tmp_path / "out.json"
    monkeypatch.setattr(core.score_vision, "score_photos",
                        lambda paths, photo_ids, profile: _gemini_rows(photo_ids))
    monkeypatch.setattr(sys, "argv", ["rank.py", "--mode", "set", "--input", str(tmp_path),
                                      "--output", str(out_file)])
    core.rank.main()
    out = json.loads(out_file.read_text())
    assert sorted(p["photo_id"] for p in out["ranked"]) == sorted(NAMES)
    assert "skipped_blurry" not in out
