"""
End to end: a fresh result shows each photo; a restored result shows placeholders.

Builds the PWA against a local test server that serves both the bundle and the
real POST /rank handler from one origin (Gemini stubbed, mode forced), then
drives an upload in headless Chromium on an emulated Android phone, using the
names an Android picker can give: an upper-case extension, no extension, and a
repeated name. Each photo has its own width, so a card showing the wrong photo
fails too. After a reload the result comes back from sessionStorage, where
previews cannot survive, so every card must show the placeholder.

Needs node and the frontend dev dependencies:
    cd frontend && npm install && npx playwright install chromium
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from api import main as api

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
DRIVER = Path(__file__).resolve().parent / "e2e" / "results_photos.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (FRONTEND / "node_modules" / "playwright").is_dir(),
    reason="needs node and frontend dev dependencies (cd frontend && npm install)",
)

# What the user picks, and the photo_id each must come back as.
NAMES = ["1000330417.JPG", "1000330418", "1000330419.jpg", "1000330419.jpg"]
IDS = ["1000330417.JPG", "1000330418", "1000330419.jpg", "1000330419_1.jpg"]
WIDTHS = [320, 336, 352, 368]  # one per photo, to tell them apart on screen


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

    port = _free_port()
    dist = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["node", "node_modules/vite/bin/vite.js", "build",
         "--outDir", str(dist), "--emptyOutDir", "--logLevel", "warn"],
        cwd=FRONTEND, check=True, timeout=300,
        env={**os.environ, "VITE_API_BASE": f"http://127.0.0.1:{port}"},
    )

    app = FastAPI()
    app.add_api_route("/rank", api.rank_endpoint, methods=["POST"])
    app.mount("/", StaticFiles(directory=dist, html=True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("test server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    server.should_exit = True
    thread.join(timeout=10)


def _fake_gemini(paths, photo_ids, profile):
    return [
        {"photo_id": pid, "subject_1_expression": 9 - i, "subject_2_expression": None,
         "expression": float(9 - i), "camera_engagement": 3 + i, "composition": 4 + i,
         "subject_focus": 7, "relative_rank": i + 1, "notes": "ok"}
        for i, pid in enumerate(photo_ids)
    ]


def _jpeg(width: int, seed: int) -> bytes:
    import cv2
    import numpy as np

    noise = np.random.default_rng(seed).integers(0, 256, (240, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", noise)
    assert ok
    return buf.tobytes()


def _drive(site: str, tmp_path: Path, monkeypatch, mode: str) -> dict:
    monkeypatch.setattr(api, "_auto_detect_mode", lambda paths: mode)
    monkeypatch.setattr(api, "score_photos", _fake_gemini)
    files = []
    for i, (name, width) in enumerate(zip(NAMES, WIDTHS)):
        path = tmp_path / f"upload-{i}.jpg"
        path.write_bytes(_jpeg(width, seed=i))
        files.append({"path": str(path), "name": name})
    proc = subprocess.run(
        ["node", str(DRIVER)], input=json.dumps({"url": site, "files": files}),
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    if "skip" in out:
        pytest.skip(f"Chromium unavailable ({out['skip']}); run: npx playwright install chromium")
    assert out["errors"] == []
    return out


@pytest.mark.parametrize("mode", ["burst", "set"])
def test_fresh_result_shows_each_photo_and_restored_shows_placeholders(
    site, tmp_path, monkeypatch, mode,
):
    out = _drive(site, tmp_path, monkeypatch, mode)
    width_of = dict(zip(IDS, WIDTHS))

    fresh = out["fresh"]
    assert sorted(c["id"] for c in fresh["cards"]) == sorted(IDS)
    for card in fresh["cards"]:
        assert card["shows"] == "photo", card
        assert card["width"] == width_of[card["id"]], card
    assert fresh["saveEnabled"]

    restored = out["restored"]
    assert sorted(c["id"] for c in restored["cards"]) == sorted(IDS)
    assert {c["shows"] for c in restored["cards"]} == {"placeholder"}
    assert not restored["saveEnabled"]


def test_switching_profile_does_not_shift_the_layout(site, tmp_path, monkeypatch):
    switcher = _drive(site, tmp_path, monkeypatch, "set")["switcher"]
    assert switcher is not None
    assert set(switcher["heroTop"]) == {"Family", "Portrait", "Travel", "Event"}
    assert len(set(switcher["heroTop"].values())) == 1, switcher["heroTop"]
    assert switcher["notes"] == 0
    # "Reset to Family" stays, for every profile but the run's own.
    assert switcher["reset"] == {"Family": False, "Portrait": True, "Travel": True, "Event": True}
