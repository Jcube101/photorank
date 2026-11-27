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
from tests.exif_jpeg import jpeg as exif_jpeg

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


def _drive(site: str, tmp_path: Path, monkeypatch, mode: str | None,
           photos: list[tuple[str, bytes]] | None = None, gemini=_fake_gemini,
           breaks: tuple[str, ...] = ()) -> dict:
    """
    Upload `photos` [(name, jpeg bytes)] (default: NAMES at WIDTHS) and read the
    results screen. `mode` forces the server's choice; None lets it decide.
    `gemini` stands in for score_photos. `breaks` makes the page's decoders
    fail (see tests/e2e/results_photos.mjs).
    """
    if mode is not None:
        monkeypatch.setattr(api, "_auto_detect_mode", lambda *args: mode)
    monkeypatch.setattr(api, "score_photos", gemini)
    if photos is None:
        photos = [(name, _jpeg(width, seed=i)) for i, (name, width) in enumerate(zip(NAMES, WIDTHS))]
    files = []
    for i, (name, data) in enumerate(photos):
        path = tmp_path / f"upload-{i}.jpg"
        path.write_bytes(data)
        files.append({"path": str(path), "name": name})
    proc = subprocess.run(
        ["node", str(DRIVER)], input=json.dumps({"url": site, "files": files, "break": list(breaks)}),
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


# --- Burst mode from the app (capture times read on the phone) ---------------

BURST_TIMES = ["2026:05:13 11:03:27", "2026:05:13 11:03:29",
               "2026:05:13 11:03:30", "2026:05:13 11:03:37"]
BURST_NAMES = ["1000330417.jpg", "1000330418.jpg", "1000330419.jpg", "1000330420.jpg"]


def _spy_on_uploads(monkeypatch) -> dict:
    """Record the capture_times field and the EXIF of each file the server got."""
    from PIL import Image

    seen = {"capture_times": None, "exif_tags": []}
    real_sent, real_ingest = getattr(api, "_sent_capture_times", None), api.ingest

    def sent(raw):
        seen["capture_times"] = raw
        return real_sent(raw)

    def ingest(paths):
        for p in paths:
            with Image.open(p) as im:
                exif = im.getexif()
                seen["exif_tags"].append(len(exif) + len(exif.get_ifd(0x8769)) + len(exif.get_ifd(0x8825)))
        return real_ingest(paths)

    monkeypatch.setattr(api, "_sent_capture_times", sent, raising=False)
    monkeypatch.setattr(api, "ingest", ingest)
    return seen


def _counting_gemini():
    calls = []

    def gemini(paths, photo_ids, profile):
        calls.append(len(photo_ids))
        return _fake_gemini(paths, photo_ids, profile)

    return gemini, calls


def test_app_reads_capture_times_so_a_burst_runs_without_gemini(site, tmp_path, monkeypatch):
    seen = _spy_on_uploads(monkeypatch)
    gemini, calls = _counting_gemini()
    photos = [(name, exif_jpeg(width=WIDTHS[i], seed=i, dto=t))  # each with GPS in its EXIF
              for i, (name, t) in enumerate(zip(BURST_NAMES, BURST_TIMES))]
    out = _drive(site, tmp_path, monkeypatch, None, photos, gemini)

    assert out["mode"] == "burst"
    assert calls == []
    assert {c["shows"] for c in out["fresh"]["cards"]} == {"photo"}
    # The phone sent exactly one thing per photo: its DateTimeOriginal.
    assert json.loads(seen["capture_times"]) == dict(zip(BURST_NAMES, BURST_TIMES))
    # The uploads themselves carry no EXIF at all, so no GPS.
    assert seen["exif_tags"] == [0, 0, 0, 0]


def test_app_photos_without_capture_times_run_set_mode(site, tmp_path, monkeypatch):
    seen = _spy_on_uploads(monkeypatch)
    gemini, calls = _counting_gemini()
    out = _drive(site, tmp_path, monkeypatch, None, gemini=gemini)
    assert out["mode"] == "set"
    assert calls == [len(NAMES)]
    assert seen["capture_times"] is None  # nothing to send, so no field at all


# --- Burst results on a phone that can't decode with createImageBitmap -------
# On the owner's Android phone (2026-11-27) every photo went up uncompressed:
# createImageBitmap rejected them, so no preview was made and every burst card
# showed the placeholder, though the phone can display the photos
# (LEARNINGS 2026-11-27). Headless Chromium decodes them, so the tests make
# its decoders fail the same way.

PHONE_NAMES = ["1000330417.jpg", "1000330418.jpg", "1000330419.jpg", "1000330420.jpg",
               "1000330421.jpg"]
PHONE_TIMES = ["2026:11:27 14:16:41", "2026:11:27 14:16:42", "2026:11:27 14:16:42",
               "2026:11:27 14:16:44", "2026:11:27 14:16:45"]
PHONE_WIDTHS = [320, 336, 352, 368, 384]
PHONE_BLUR = [0.8, 0, 1.0, 0.9, 1.1]  # distinct scores, ranked in another order than picked


def _phone_burst() -> list[tuple[str, bytes]]:
    return [(name, exif_jpeg(width=w, seed=i, dto=t, blur=b))
            for i, (name, t, w, b) in enumerate(zip(PHONE_NAMES, PHONE_TIMES, PHONE_WIDTHS, PHONE_BLUR))]


@pytest.mark.parametrize("breaks", [("createImageBitmap",), ("createImageBitmap", "imgDecode")],
                         ids=["bitmap_rejected", "no_decoder"])
def test_burst_shows_each_photo_when_the_phone_cannot_decode_it(site, tmp_path, monkeypatch, breaks):
    seen = _spy_on_uploads(monkeypatch)
    gemini, calls = _counting_gemini()
    out = _drive(site, tmp_path, monkeypatch, None, _phone_burst(), gemini, breaks)

    assert out["mode"] == "burst"
    assert calls == []
    width_of = dict(zip(PHONE_NAMES, PHONE_WIDTHS))
    fresh = out["fresh"]
    assert sorted(c["id"] for c in fresh["cards"]) == sorted(PHONE_NAMES)
    for card in fresh["cards"]:
        assert card["shows"] == "photo", card
        assert card["width"] == width_of[card["id"]], card
    assert fresh["saveEnabled"]
    assert {c["shows"] for c in out["restored"]["cards"]} == {"placeholder"}
    if breaks == ("createImageBitmap",):
        # Decoded through an <img> instead, so still compressed: no EXIF went up.
        assert seen["exif_tags"] == [0] * len(PHONE_NAMES)


def test_a_photo_the_browser_cannot_display_shows_the_placeholder(site, tmp_path, monkeypatch):
    """Like HEIC on Android Chrome: the server scores it, the phone can't show it."""
    import cv2
    import numpy as np

    noise = np.random.default_rng(9).integers(0, 256, (240, 300, 3), dtype=np.uint8)
    ok, tiff = cv2.imencode(".tiff", noise)  # noise is sharp: it passes the blur gate
    assert ok
    photos = [(NAMES[0], _jpeg(WIDTHS[0], seed=0)), ("1000330499.jpg", tiff.tobytes()),
              (NAMES[2], _jpeg(WIDTHS[2], seed=2))]
    cards = {c["id"]: c for c in _drive(site, tmp_path, monkeypatch, "burst", photos)["fresh"]["cards"]}
    assert cards["1000330499.jpg"]["shows"] == "placeholder"  # not a broken image
    assert cards[NAMES[0]]["shows"] == cards[NAMES[2]]["shows"] == "photo"


# Burst-note wording (frontend/src/lib/notes.js) -> the axis it is about.
BEST = {"Sharpest face": "face_sharpness", "Sharpest overall": "sharpness",
        "Best-exposed face": "face_exposure", "Best exposure": "exposure"}
BETTER = {"Sharper face": "face_sharpness", "Sharper overall": "sharpness",
          "Better-exposed face": "face_exposure", "Better exposure": "exposure"}
WORSE = {"softer face": "face_sharpness", "softer overall": "sharpness",
         "face less well exposed": "face_exposure", "weaker exposure": "exposure"}


def _shown(photo: dict, axis: str) -> float:
    return round(photo["score_breakdown"][axis]["raw"], 1)  # as the breakdown shows it


def test_burst_notes_are_true_for_each_photo(site, tmp_path, monkeypatch):
    out = _drive(site, tmp_path, monkeypatch, None, _phone_burst())
    assert out["mode"] == "burst"
    ranked = out["response"]["ranked"]
    note = {c["id"]: c["note"] for c in out["fresh"]["cards"]}
    top, rest = ranked[0], ranked[1:]

    # Only №1 is the best at anything in this set, and it really is.
    best = note[top["photo_id"]].removesuffix(" in this set.")
    assert best in BEST, note
    assert all(_shown(top, BEST[best]) > _shown(p, BEST[best]) for p in rest), (note, ranked)

    # Every other photo is described against №1, truthfully.
    for p in rest:
        text = note[p["photo_id"]]
        assert "in this set" not in text, note
        if ", but " in text:
            better, worse = text.removesuffix(".").split(" than №1, but ")
            assert _shown(p, BETTER[better]) > _shown(top, BETTER[better]), (text, p, top)
        else:
            worse = text.removesuffix(" than №1.").lower()
        assert _shown(p, WORSE[worse]) < _shown(top, WORSE[worse]), (text, p, top)
    assert len(set(note.values())) > 1
