"""
Burst mode ranks every readable photo: no fixed blur gate. A frame is flagged
"noticeably softer than №1" when its blur_raw (full-image Laplacian variance)
is below BURST_SOFTER_RATIO x №1's (core/rank.py, where the 0.2 is explained).

The old gate dropped any frame below blur_raw 100, which cut an indoor selfie
burst's frames at 59, 69 and 99 that the owner judged fine.
"""

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from api import main as api
from tests.exif_jpeg import jpeg

NAMES = ["1000330417.jpg", "1000330418.jpg", "1000330419.jpg", "1000330420.jpg", "1000330421.jpg"]
# Gaussian sigma per frame: 0, 0.35 and 0.4 stay within the ratio (about 1.0,
# 0.84 and 0.64 of the sharpest); 2.2 and 2.6 are far softer, and below the old
# fixed gate of 100, which dropped them.
BLUR = [0, 0.35, 0.4, 2.2, 2.6]
REPO = Path(__file__).resolve().parent.parent


def _burst(photos: list[bytes]) -> dict:
    files = [("files", (n, p, "image/jpeg")) for n, p in zip(NAMES, photos)]
    res = TestClient(api.app).post("/rank", files=files, data={"mode": "burst"})
    assert res.status_code == 200, res.text
    return res.json()


def test_burst_ranks_every_readable_photo():
    out = _burst([jpeg(seed=i, blur=b) for i, b in enumerate(BLUR)])
    assert sorted(p["photo_id"] for p in out["ranked"]) == sorted(NAMES)
    assert out["left_out"] == []
    assert out["scored_photos"] == out["total_photos"] == 5
    assert min(p["blur_raw"] for p in out["ranked"]) < 100  # below the old gate, still ranked


def test_burst_flags_only_frames_far_softer_than_the_top():
    out = _burst([jpeg(seed=i, blur=b) for i, b in enumerate(BLUR)])
    assert out["softer_ratio"] == 0.2
    top, *rest = out["ranked"]
    assert top["softer_than_top"] is False
    for photo in rest:
        assert photo["softer_than_top"] == (photo["blur_raw"] < 0.2 * top["blur_raw"]), photo
    flagged = {p["photo_id"] for p in out["ranked"] if p["softer_than_top"]}
    assert flagged == {NAMES[3], NAMES[4]}  # the two heavily blurred frames, and only those


def test_flag_softer_is_relative_to_number_one():
    from core.rank import BURST_SOFTER_RATIO, flag_softer

    assert BURST_SOFTER_RATIO == 0.2
    def ranked(*blur_raw):
        return flag_softer([{"blur_raw": b} for b in blur_raw])

    assert [p["softer_than_top"] for p in ranked(1000, 360, 201, 199, 50)] == [
        False, False, False, True, True]
    # The owner's selfie frames (59, 69, 99): not flagged unless №1 is over 5x sharper.
    assert not any(p["softer_than_top"] for p in ranked(250, 99, 69, 59))
    assert [p["softer_than_top"] for p in ranked(500, 99, 69, 59)] == [False, True, True, True]
    # №1 is never flagged, even when another frame is sharper.
    assert [p["softer_than_top"] for p in ranked(100, 1000, 19)] == [False, False, True]
    assert flag_softer([]) == []


def test_cli_burst_also_ranks_every_photo(tmp_path):
    for name, blur in zip(NAMES, BLUR):
        (tmp_path / name).write_bytes(jpeg(seed=len(name) + int(blur * 10), blur=blur))
    out_file = tmp_path / "out.json"
    subprocess.run(
        [sys.executable, "core/rank.py", "--mode", "burst", "--input", str(tmp_path),
         "--output", str(out_file)],
        cwd=REPO, env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
        check=True, capture_output=True, timeout=120,
    )
    out = json.loads(out_file.read_text())
    assert len(out["ranked"]) == 5
    assert {p["photo_id"] for p in out["ranked"] if p["softer_than_top"]} == {NAMES[3], NAMES[4]}
