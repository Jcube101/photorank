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
from tests.exif_jpeg import (
    COMMENT_TEXT,
    GPS_LATITUDE,
    IPTC_TEXT,
    TRAILER_TEXT,
    XMP_TEXT,
    markers,
)
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
def built(tmp_path_factory):
    """The PWA, built once for the module: (dist dir, the port its API points at)."""
    port = _free_port()
    dist = tmp_path_factory.mktemp("dist")
    subprocess.run(
        ["node", "node_modules/vite/bin/vite.js", "build",
         "--outDir", str(dist), "--emptyOutDir", "--logLevel", "warn"],
        cwd=FRONTEND, check=True, timeout=300,
        env={**os.environ, "VITE_API_BASE": f"http://127.0.0.1:{port}"},
    )
    return dist, port


@pytest.fixture(scope="module")
def site(built):
    yield from _serve(*built)


@pytest.fixture
def redeploy_site(site, built):
    """The site, for a test that redeploys it; its index.html is put back after."""
    index = built[0] / "index.html"
    original = index.read_text()
    try:
        yield site, built[0]
    finally:
        index.write_text(original)


def _serve(dist: Path, port: int):
    """Serve the bundle and the real POST /rank handler from one origin."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles

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
           breaks: tuple[str, ...] = (), redeploy: Path | None = None) -> dict:
    """
    Upload `photos` [(name, bytes) or (name, bytes, MIME type)] (default: NAMES
    at WIDTHS) and read the results screen. `mode` forces the server's choice; None lets it decide.
    `gemini` stands in for score_photos. `breaks` makes the page's decoders
    fail, and `redeploy` (the served dist) deploys a new build while the app
    stays open (see tests/e2e/results_photos.mjs).
    """
    if mode is not None:
        monkeypatch.setattr(api, "_auto_detect_mode", lambda *args: mode)
    monkeypatch.setattr(api, "score_photos", gemini)
    if photos is None:
        photos = [(name, _jpeg(width, seed=i)) for i, (name, width) in enumerate(zip(NAMES, WIDTHS))]
    files = []
    for i, (name, data, *mime) in enumerate(photos):
        path = tmp_path / f"upload-{i}.jpg"
        path.write_bytes(data)
        files.append({"path": str(path), "name": name, "type": mime[0] if mime else "image/jpeg"})
    proc = subprocess.run(
        ["node", str(DRIVER)], input=json.dumps({"url": site, "files": files, "break": list(breaks),
                          "redeploy": {"dist": str(redeploy)} if redeploy else None}),
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
    assert fresh["saveLook"] == {"opacity": 1, "cursor": "pointer"}

    restored = out["restored"]
    assert sorted(c["id"] for c in restored["cards"]) == sorted(IDS)
    assert {c["shows"] for c in restored["cards"]} == {"placeholder"}
    assert not restored["saveEnabled"]
    # ... and it looks disabled, not just behaves so.
    assert restored["saveLook"]["opacity"] <= 0.5
    assert restored["saveLook"]["cursor"] == "not-allowed"


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
    """Record the capture_times and orientations fields, and the bytes and EXIF of each file."""
    from PIL import Image

    seen = {"capture_times": None, "orientations": None, "exif_tags": [], "raw": []}
    real_sent, real_ingest = getattr(api, "_sent_capture_times", None), api.ingest
    real_turn = getattr(api, "_sent_orientations", None)

    def sent(raw):
        seen["capture_times"] = raw
        return real_sent(raw)

    def turn(raw):
        seen["orientations"] = raw
        return real_turn(raw)

    def ingest(paths, **kwargs):
        for p in paths:
            seen["raw"].append(Path(p).read_bytes())
            with Image.open(p) as im:
                exif = im.getexif()
                seen["exif_tags"].append(len(exif) + len(exif.get_ifd(0x8769)) + len(exif.get_ifd(0x8825)))
        return real_ingest(paths, **kwargs)

    monkeypatch.setattr(api, "_sent_capture_times", sent, raising=False)
    monkeypatch.setattr(api, "_sent_orientations", turn, raising=False)
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


# --- When compression fails, no metadata leaves the phone --------------------

def _gps_bytes() -> list[bytes]:
    import struct
    return [struct.pack(bo + "II", v, 1) for bo in "<>" for v in GPS_LATITUDE]


def test_failed_compression_sends_no_gps_and_no_exif(site, tmp_path, monkeypatch):
    """
    Phone photos with GPS (and XMP, IPTC, a comment, a Motion Photo trailer),
    with compression forced to fail. The server must get the photos with none
    of it, and burst mode must still trigger: the capture times are read from
    the originals before they are cleaned.
    """
    seen = _spy_on_uploads(monkeypatch)
    gemini, calls = _counting_gemini()
    photos = [(name, exif_jpeg(width=w, seed=i, dto=t, blur=b, all_metadata=True))
              for i, (name, t, w, b) in enumerate(zip(PHONE_NAMES, PHONE_TIMES, PHONE_WIDTHS, PHONE_BLUR))]
    for _, data in photos:
        assert _gps_bytes()[0] in data  # the originals carry GPS
    out = _drive(site, tmp_path, monkeypatch, None, photos, gemini,
                 breaks=("createImageBitmap", "imgDecode"))

    assert len(seen["raw"]) == len(PHONE_NAMES)
    assert seen["exif_tags"] == [0] * len(PHONE_NAMES)
    for data in seen["raw"]:
        for planted in (*_gps_bytes(), b"Exif", XMP_TEXT, IPTC_TEXT, COMMENT_TEXT, TRAILER_TEXT):
            assert planted not in data, planted
        assert not {0xE1, 0xE2, 0xED, 0xFE} & set(markers(data))  # no EXIF/XMP, ICC, IPTC, comment
        assert data.endswith(b"\xff\xd9")  # nothing after the image
    assert json.loads(seen["capture_times"]) == dict(zip(PHONE_NAMES, PHONE_TIMES))
    assert out["mode"] == "burst"
    assert calls == []
    assert {c["shows"] for c in out["fresh"]["cards"]} == {"photo"}


def _undecodable() -> bytes:
    """Stands in for a HEIC on Android Chrome: no browser decoder, not a JPEG."""
    import cv2
    import numpy as np

    ok, tiff = cv2.imencode(".tiff", np.random.default_rng(9).integers(0, 256, (240, 300, 3), dtype=np.uint8))
    assert ok
    return tiff.tobytes()


def test_a_photo_that_cannot_be_prepared_is_named_and_not_sent(site, tmp_path, monkeypatch):
    seen = _spy_on_uploads(monkeypatch)
    photos = [(NAMES[0], _jpeg(WIDTHS[0], seed=0)),
              ("IMG_0042.HEIC", _undecodable(), "image/heic"),
              (NAMES[2], _jpeg(WIDTHS[2], seed=2))]
    out = _drive(site, tmp_path, monkeypatch, "burst", photos)

    assert len(seen["raw"]) == 2  # the HEIC never left the phone
    fresh = out["fresh"]
    assert sorted(c["id"] for c in fresh["cards"]) == sorted([NAMES[0], NAMES[2]])
    assert {c["shows"] for c in fresh["cards"]} == {"photo"}
    assert fresh["notice"] == "Couldn't prepare IMG_0042.HEIC for upload, so it wasn't ranked."
    assert out["restored"]["notice"] == fresh["notice"]  # still named after a restore


def test_too_few_photos_left_shows_a_message_and_sends_nothing(site, tmp_path, monkeypatch):
    photos = [(NAMES[0], _jpeg(WIDTHS[0], seed=0)), ("IMG_0042.HEIC", _undecodable(), "image/heic")]
    out = _drive(site, tmp_path, monkeypatch, "burst", photos)
    assert out["rankRequests"] == 0
    assert out["fresh"]["message"] == {
        "eyebrow": "Couldn't prepare",
        "title": "Not enough photos to rank.",
        "body": "Couldn't prepare IMG_0042.HEIC for upload, so only 1 photo is left to rank. "
                "PhotoRank needs at least 2.",
    }


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


# --- A sideways photo is scored and shown upright, however it was prepared ---
# Cameras often store pixels sideways with an EXIF Orientation tag. Compression
# applies it (the browser decodes upright); stripping drops it, so for stripped
# uploads the app sends the orientation and the server turns them upright.

SIDEWAYS = 6  # stored 320x240; shown 240x320, turned 90 degrees clockwise


def _sideways_burst() -> list[tuple[str, bytes]]:
    return [(name, exif_jpeg(width=320, seed=i, dto=t, blur=1.0, orientation=SIDEWAYS, all_metadata=True))
            for i, (name, t) in enumerate(zip(PHONE_NAMES[:3], PHONE_TIMES[:3]))]


def _spy_on_scoring(monkeypatch) -> dict:
    """The pixels each photo was scored on, by photo_id."""
    import cv2

    scored, real = {}, api.compute_burst_scores

    def spy(path, photo_id=None):
        scored[photo_id] = cv2.imread(str(path))
        return real(path, photo_id=photo_id)

    monkeypatch.setattr(api, "compute_burst_scores", spy)
    return scored


def _upright(data: bytes):
    import cv2
    import numpy as np

    stored = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    return cv2.rotate(stored, cv2.ROTATE_90_CLOCKWISE)


def _correlation(a, b) -> float:
    import cv2
    import numpy as np

    a, b = (cv2.cvtColor(x, cv2.COLOR_BGR2GRAY).astype(float).ravel() for x in (a, b))
    return float(np.corrcoef(a, b)[0, 1])


@pytest.mark.parametrize("breaks", [(), ("createImageBitmap",), ("createImageBitmap", "imgDecode")],
                         ids=["compressed", "compressed_via_img", "stripped"])
def test_a_sideways_photo_is_scored_and_shown_upright(site, tmp_path, monkeypatch, breaks):
    seen = _spy_on_uploads(monkeypatch)
    scored = _spy_on_scoring(monkeypatch)
    photos = _sideways_burst()
    out = _drive(site, tmp_path, monkeypatch, None, photos, breaks=breaks)
    assert out["mode"] == "burst"

    # Only a stripped upload sends its orientation; a compressed one is upright
    # already, and turning it again would put it on its side.
    stripped = "imgDecode" in breaks
    sent = json.loads(seen["orientations"]) if seen["orientations"] else None
    assert sent == ({name: SIDEWAYS for name, _ in photos} if stripped else None)
    if stripped:
        assert seen["exif_tags"] == [0] * len(photos)  # the value went in its own field

    for name, data in photos:
        upright = _upright(data)
        assert scored[name].shape[:2] == upright.shape[:2] == (320, 240), name
        assert _correlation(scored[name], upright) > 0.9, name
    assert {c["width"] for c in out["fresh"]["cards"]} == {240}  # shown upright too


# --- Every photo picked is on screen, ranked or named as left out -------------
# 2026-11-27 18:11: a 5-photo burst showed 2 cards and nothing else. The
# server's blur gate had dropped 3 frames and the app said nothing about them.

BLURRY_BURST = [0, 0.6, 2.2, 2.4, 2.6]  # blur sigma: the last 3 fall below the gate


def test_a_burst_shows_every_photo_picked(site, tmp_path, monkeypatch):
    photos = [(name, exif_jpeg(width=w, seed=i, dto=t, blur=b))
              for i, (name, t, w, b) in enumerate(zip(PHONE_NAMES, PHONE_TIMES, PHONE_WIDTHS, BLURRY_BURST))]
    out = _drive(site, tmp_path, monkeypatch, None, photos)
    assert out["mode"] == "burst"
    sharp, blurry = PHONE_NAMES[:2], PHONE_NAMES[2:]

    for screen in (out["fresh"], out["restored"]):
        assert sorted(c["id"] for c in screen["cards"]) == sorted(sharp)
        assert [c["id"] for c in screen["leftOut"]] == blurry  # in the order picked
        for card in screen["leftOut"]:
            assert card["reason"].startswith("Too blurry to rank: blur check "), card
            assert card["reason"].endswith(", needs 100."), card
        assert screen["notice"] == f"Too blurry to rank: {blurry[0]}, {blurry[1]} and {blurry[2]}."
        assert screen["meta"] == "Burst · 5 photos"  # not "Custom"
        assert screen["rankedBy"] == "Burst mode"
    assert {c["shows"] for c in out["fresh"]["leftOut"]} == {"photo"}  # each one's own photo
    left = {e["photo_id"]: e for e in out["response"]["left_out"]}
    assert sorted(left) == sorted(blurry)
    assert all(e["reason"] == "blurry" and e["blur_raw"] < 100 for e in left.values())


# --- A deploy reaches an app that stays open ----------------------------------
# Android keeps an installed PWA alive in memory, so reopening it doesn't
# reload it: on 2026-11-27 the owner's phone ran a bundle two deploys old.

def test_a_new_deploy_reaches_an_open_app(redeploy_site, tmp_path, monkeypatch):
    site, dist = redeploy_site
    out = _drive(site, tmp_path, monkeypatch, "burst", redeploy=dist)
    r = out["redeploy"]
    # Coming back to the front on a result: it waits, so the result isn't lost ...
    assert r["onResults"]["stillShowsResults"]
    assert r["onResults"]["running"] != r["first"]
    # ... and "New batch" starts on the new build.
    assert r["afterNewBatch"] == r["first"]
    # Back to the front on the upload screen, nothing picked: it reloads.
    assert r["afterResume"] == r["second"]
