"""
Every uploaded photo comes back exactly once: in `ranked`, or in `left_out`
with the reason (SPECS §6.1), so the app can name it.

On 2026-11-27 a 5-photo burst came back as 2 ranked photos and the count
`skipped_blurry: 3`: the blur gate had dropped 3 frames, and with no names the
app could not say which. And the app's own wording (frontend/src/lib/leftOut.js),
run under Node.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import main as api
from tests.exif_jpeg import jpeg

NAMES = ["1000330417.jpg", "1000330418.jpg", "1000330419.jpg", "1000330420.jpg", "1000330421.jpg"]
BLUR = [0, 0.6, 2.2, 2.4, 2.6]  # Gaussian sigma: the last 3 fall below the blur gate
LEFT_OUT_JS = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "leftOut.js"


def _gemini(paths, photo_ids, profile):
    return [{"photo_id": pid, "subject_1_expression": 7, "subject_2_expression": None,
             "expression": 7.0, "camera_engagement": 7, "composition": 7, "subject_focus": 7,
             "relative_rank": i + 1, "notes": "ok"} for i, pid in enumerate(photo_ids)]


def _rank(monkeypatch, photos: list[bytes], mode: str) -> dict:
    monkeypatch.setattr(api, "score_photos", _gemini)
    files = [("files", (n, p, "image/jpeg")) for n, p in zip(NAMES, photos)]
    res = TestClient(api.app).post("/rank", files=files, data={"mode": mode})
    assert res.status_code == 200, res.text
    return res.json()


def _accounted_once(out: dict, uploaded: list[str]) -> None:
    ranked = [p["photo_id"] for p in out["ranked"]]
    left = [e["photo_id"] for e in out["left_out"]]
    assert sorted(ranked + left) == sorted(uploaded)  # each exactly once


@pytest.mark.parametrize("mode", ["burst", "set"])
def test_blurry_photos_are_named_with_their_blur_score(monkeypatch, mode):
    photos = [jpeg(seed=i, blur=b) for i, b in enumerate(BLUR)]
    out = _rank(monkeypatch, photos, mode)
    _accounted_once(out, NAMES)
    assert sorted(p["photo_id"] for p in out["ranked"]) == sorted(NAMES[:2])
    assert [e["photo_id"] for e in out["left_out"]] == NAMES[2:]  # in upload order
    for entry in out["left_out"]:
        assert entry["reason"] == "blurry"
        assert entry["blur_raw"] < out["blur_threshold"]
    assert out["skipped_blurry"] == 3


@pytest.mark.parametrize("mode", ["burst", "set"])
def test_nothing_is_left_out_when_the_blur_gate_is_bypassed(monkeypatch, mode):
    out = _rank(monkeypatch, [jpeg(seed=i, blur=2.5) for i in range(5)], mode)
    assert out["blur_gate_bypassed"]
    assert out["left_out"] == []
    _accounted_once(out, NAMES)


def test_an_unreadable_upload_is_named(monkeypatch):
    photos = [jpeg(seed=i) for i in range(4)] + [b"\xff\xd8 not really a jpeg"]
    out = _rank(monkeypatch, photos, "burst")
    _accounted_once(out, NAMES)
    assert out["left_out"] == [{"photo_id": NAMES[4], "reason": "unreadable"}]


def test_a_photo_that_fails_scoring_is_named(monkeypatch):
    real = api.compute_burst_scores

    def flaky(path, photo_id=None):
        if photo_id == NAMES[1]:
            raise ValueError("Could not read image")
        return real(path, photo_id=photo_id)

    monkeypatch.setattr(api, "compute_burst_scores", flaky)
    out = _rank(monkeypatch, [jpeg(seed=i) for i in range(5)], "burst")
    _accounted_once(out, NAMES)
    assert out["left_out"] == [{"photo_id": NAMES[1], "reason": "unscorable"}]


# --- The app's wording (lib/leftOut.js) ---------------------------------------

NODE_SCRIPT = """
const m = await import(process.argv[1]);
const fs = await import("node:fs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
console.log(JSON.stringify(cases.map((r) => {
  const left = m.leftOutPhotos(r);
  return { left, notice: m.leftOutNotice(left), reasons: left.map((e) => m.leftOutReason(e, 100)) };
})));
"""


def _left_out_js(results: list[dict]) -> list[dict]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    proc = subprocess.run(["node", "--input-type=module", "-e", NODE_SCRIPT, LEFT_OUT_JS.as_uri()],
                          input=json.dumps(results), capture_output=True, text=True, check=True, timeout=60)
    return json.loads(proc.stdout)


def test_the_app_names_every_photo_not_ranked():
    result = {
        "ranked": [{"photo_id": "a.jpg"}, {"photo_id": "b.jpg"}],
        "left_out": [{"photo_id": "c.jpg", "reason": "blurry", "blur_raw": 41.6},
                     {"photo_id": "d.jpg", "reason": "unreadable"}],
        "unprepared": ["IMG_0042.HEIC"],
        # e.jpg was uploaded but is in neither list: still named.
        "uploaded": ["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg"],
    }
    (got,) = _left_out_js([result])
    assert [e["photo_id"] for e in got["left"]] == ["IMG_0042.HEIC", "c.jpg", "d.jpg", "e.jpg"]
    assert got["notice"] == [
        "Couldn't prepare IMG_0042.HEIC for upload, so it wasn't ranked.",
        "Too blurry to rank: c.jpg.",
        "The server couldn't read d.jpg.",
        "Not returned by the server: e.jpg.",
    ]
    assert got["reasons"] == [
        "Couldn't be prepared for upload, so it wasn't sent.",
        "Too blurry to rank: blur check 42, needs 100.",
        "The server couldn't read this photo.",
        "The server didn't return this photo.",
    ]


def test_a_result_with_nothing_left_out_names_nothing():
    (got,) = _left_out_js([{"ranked": [{"photo_id": "a.jpg"}, {"photo_id": "b.jpg"}],
                            "left_out": [], "uploaded": ["a.jpg", "b.jpg"]}])
    assert got == {"left": [], "notice": [], "reasons": []}
