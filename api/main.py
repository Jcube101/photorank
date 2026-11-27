"""
Phase 2 — FastAPI wrapper around the PhotoRank core pipeline.

POST /rank   — rank 2–20 uploaded images
GET  /health — liveness / config check

Temp files live in /tmp/photorank_{uuid}/ and are deleted via try/finally
even when scoring fails. Photos never persist past the request lifecycle.
"""

import json
import logging
import os
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv()

from core.group import read_capture_time
from core.ingest import cleanup, ingest
from core.profiles import BURST_WEIGHTS, PROFILES, validate_weights
from core.rank import _merge, _rank_burst, rank_photos
from core.score_burst import compute_burst_scores
from core.score_tech import compute_technical_scores
from core.score_vision import score_photos

_BLUR_THRESHOLD           = 100.0
_BURST_MAX_PHOTOS         = 6
_BURST_TIMESTAMP_WINDOW_S = 10
_VALID_PROFILES           = set(PROFILES.keys()) | {"custom"}

# EXIF DateTimeOriginal, as the PWA sends it in `capture_times`
_EXIF_TIME_FORMAT = "%Y:%m:%d %H:%M:%S"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("photorank.api")


def _reject(status: int, detail: str) -> HTTPException:
    """
    Build an HTTPException while logging the reason so the cause of every 4xx/5xx
    on /rank lands in journald. Detail strings carry only counts, profile names,
    and mode/blur messages — never image content — so this is privacy-safe.
    """
    logger.warning("/rank rejected: %d — %s", status, detail)
    return HTTPException(status, detail=detail)

app = FastAPI(title="PhotoRank API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://job-joseph.com",
        "http://job-joseph.com",
        "http://localhost",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8080",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": "1.0",
        "gemini_key_set": bool(os.getenv("GEMINI_API_KEY")),
    }


# ---------------------------------------------------------------------------
# Capture times (burst auto-detection)
# ---------------------------------------------------------------------------
# The PWA re-encodes every photo before upload, which strips all EXIF, so it
# reads each photo's DateTimeOriginal on the phone first and sends it in the
# `capture_times` field: {photo_id: "YYYY:MM:DD HH:MM:SS"}. Uploads of original
# files (curl, scripts) still carry EXIF, so a photo without a sent time falls
# back to its own DateTimeOriginal. Capture times are only compared here: they
# are never logged, returned or stored.

def _sent_capture_times(raw: Optional[str]) -> dict[str, str]:
    """The capture_times field as {photo_id: time}. Anything malformed is ignored."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


# The PWA uploads a photo with its metadata stripped when it can't compress it,
# which drops the EXIF orientation, so a rotated photo arrives sideways. For
# those photos only, it sends the orientation in the `orientations` field:
# {photo_id: 2-8}. Ingest turns them upright before scoring, so face detection
# sees faces the right way up. Never logged, returned or stored.

def _sent_orientations(raw: Optional[str]) -> dict[str, int]:
    """The orientations field as {photo_id: 1-8}. Anything malformed is ignored."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if isinstance(k, str) and type(v) is int and 1 <= v <= 8}


def _parse_capture_time(value: Optional[str]) -> Optional[int]:
    """'YYYY:MM:DD HH:MM:SS' (EXIF DateTimeOriginal) as epoch ms, or None."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.strptime(value, _EXIF_TIME_FORMAT)
    except ValueError:
        return None
    return int(dt.replace(tzinfo=UTC).timestamp()) * 1000


def _capture_time_ms(path: Path, photo_id: str, sent: dict[str, str]) -> Optional[int]:
    """The client-sent time for this photo, else its own EXIF DateTimeOriginal."""
    ms = _parse_capture_time(sent.get(photo_id))
    if ms is None:
        try:
            ms, _source = read_capture_time(path)  # reads the Exif IFD, not IFD0
        except Exception:  # e.g. Pillow's DecompressionBombError on a 200 MP original
            ms = None      # no usable time: set mode, never a 500
    return ms


def _auto_detect_mode(uploads: list[tuple[Path, str]], sent: dict[str, str]) -> str:
    """
    Return 'burst' when <=6 photos all have capture times within 10 s of each
    other. Falls back to 'set' if there are more photos or any time is missing.
    `uploads` is [(path on disk, photo_id)]; `sent` is from _sent_capture_times.
    """
    if len(uploads) > _BURST_MAX_PHOTOS:
        return "set"
    times = [_capture_time_ms(path, photo_id, sent) for path, photo_id in uploads]
    if any(t is None for t in times):
        return "set"
    span_ms = max(times) - min(times)  # type: ignore[type-var]
    return "burst" if span_ms <= _BURST_TIMESTAMP_WINDOW_S * 1000 else "set"


# ---------------------------------------------------------------------------
# POST /rank
# ---------------------------------------------------------------------------

def _photo_ids(filenames: list[Optional[str]]) -> list[str]:
    """
    The photo_id for each upload: its filename exactly as the client sent it.

    The PWA finds each photo's on-screen preview by this name, so it must come
    back unchanged: no case folding and no added extension. (Android pickers
    send names like "1000330417.JPG" or a bare "1000330417"; rewriting them
    made every result show the placeholder.) Only a missing name or a repeat
    changes: a repeat gets "_N" before its extension, using the lowest N not
    already taken in this batch.
    """
    used: set[str] = set()
    ids: list[str] = []
    for raw in filenames:
        name = raw or "image.jpg"
        if name in used:
            stem, dot, ext = name.rpartition(".")
            if not stem:  # no extension, or a name like ".hidden"
                stem, dot, ext = name, "", ""
            n = 1
            while f"{stem}_{n}{dot}{ext}" in used:
                n += 1
            name = f"{stem}_{n}{dot}{ext}"
        used.add(name)
        ids.append(name)
    return ids


@app.post("/rank")
async def rank_endpoint(
    files: list[UploadFile] = File(...),
    profile: str = Form("family"),
    mode: Optional[str] = Form(None),
    weights: Optional[str] = Form(None),
    capture_times: Optional[str] = Form(None),
    orientations: Optional[str] = Form(None),
) -> JSONResponse:
    # --- Input validation ---
    _t_req = time.perf_counter()
    logger.info(
        "/rank received: files=%d profile=%s mode=%s",
        len(files), profile, mode if mode is not None else "auto",
    )

    if not 2 <= len(files) <= 20:
        raise _reject(422, f"Expected 2–20 images, got {len(files)}.")

    if profile not in _VALID_PROFILES:
        raise _reject(
            422,
            f"Unknown profile '{profile}'. Valid: {sorted(_VALID_PROFILES)}",
        )

    if mode is not None and mode not in ("burst", "set"):
        raise _reject(422, "mode must be 'burst' or 'set'.")

    custom_weights: Optional[dict] = None
    if profile == "custom":
        if not weights:
            raise _reject(422, "weights required when profile is 'custom'.")
        try:
            custom_weights = json.loads(weights)
        except json.JSONDecodeError as exc:
            raise _reject(422, f"Invalid weights JSON: {exc}")
        try:
            validate_weights(custom_weights)
        except ValueError as exc:
            raise _reject(422, str(exc))

    # --- Save uploads to isolated temp dir ---
    upload_dir = Path(f"/tmp/photorank_{uuid.uuid4().hex}")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # (saved_path_on_disk, photo_id)
    upload_info: list[tuple[Path, str]] = []

    try:
        for upload, display in zip(files, _photo_ids([u.filename for u in files])):
            # The disk name is a uuid so same-named uploads never collide in the
            # dir; its suffix is only a hint (decoders read the content).
            suffix = Path(display).suffix.lower() or ".jpg"
            dest = upload_dir / f"{uuid.uuid4().hex}{suffix}"
            dest.write_bytes(await upload.read())
            upload_info.append((dest, display))

        upload_paths   = [info[0] for info in upload_info]
        uuid_to_display = {p.name: name for p, name in upload_info}

        # --- Mode resolution (sent capture times, else raw EXIF before ingest strips it) ---
        resolved_mode = mode if mode is not None else _auto_detect_mode(
            upload_info, _sent_capture_times(capture_times),
        )
        logger.info("/rank resolved mode=%s for %d file(s)", resolved_mode, len(upload_paths))

        # --- Ingest: cv2.imread + cv2.imwrite strips EXIF automatically ---
        _t_ingest = time.perf_counter()
        try:
            turn = _sent_orientations(orientations)
            photos, ingest_temp_dir = ingest(
                upload_paths,
                orientations={p: turn[name] for p, name in upload_info if name in turn},
            )
        except ValueError as exc:
            raise _reject(422, str(exc))
        logger.info("[perf] ingest: %.2fs (%d photos)", time.perf_counter() - _t_ingest, len(photos))

        try:
            # Replace uuid-based photo_ids with original display names
            for photo in photos:
                photo["photo_id"] = uuid_to_display.get(
                    photo["photo_id"], photo["photo_id"]
                )

            # --- Scoring ---
            if resolved_mode == "burst":
                output = _run_burst(photos)
            else:
                effective_weights = (
                    custom_weights if profile == "custom" else PROFILES[profile]
                )
                output = _run_set(photos, profile, effective_weights)

        finally:
            cleanup(ingest_temp_dir)

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error in /rank")
        raise HTTPException(500, detail="Internal server error.")
    finally:
        shutil.rmtree(upload_dir, ignore_errors=True)

    logger.info("[perf] /rank TOTAL: %.2fs", time.perf_counter() - _t_req)
    return JSONResponse(content=output)


# ---------------------------------------------------------------------------
# Scoring sub-routines (called only from rank_endpoint, after EXIF stripped)
# ---------------------------------------------------------------------------

def _run_burst(photos: list[dict]) -> dict:
    all_scores: list[dict] = []
    for photo in photos:
        try:
            score = compute_burst_scores(photo["path"], photo_id=photo["photo_id"])
            all_scores.append(score)
        except ValueError as exc:
            logger.warning("burst scoring skipped a photo: %s", exc)

    if not all_scores:
        raise _reject(422, "No images could be scored.")

    sharp      = [s for s in all_scores if s["blur_raw"] >= _BLUR_THRESHOLD]
    blurry_ids = [s["photo_id"] for s in all_scores if s["blur_raw"] < _BLUR_THRESHOLD]

    # The blur gate THINS a set (drop soft frames when sharp ones exist); it must
    # not refuse to rank. If nothing clears the threshold — common for a small,
    # uniformly-soft burst — still rank everything and surface the least-blurry.
    blur_gate_bypassed = not sharp
    if blur_gate_bypassed:
        logger.warning(
            "burst: all %d image(s) below blur threshold (%.1f); ranking all anyway",
            len(all_scores), _BLUR_THRESHOLD,
        )
        sharp = all_scores

    ranked = _rank_burst(sharp)
    for item in ranked:
        item.pop("path", None)
    return {
        "mode":               "burst",
        "burst_weights":      BURST_WEIGHTS,
        "blur_threshold":     _BLUR_THRESHOLD,
        "blur_gate_bypassed": blur_gate_bypassed,
        "total_photos":       len(photos),
        "scored_photos":      len(sharp),
        "skipped_blurry":     0 if blur_gate_bypassed else len(blurry_ids),
        "ranked":             ranked,
    }


def _run_set(photos: list[dict], profile: str, weights: dict) -> dict:
    _t = time.perf_counter()
    all_technical: list[dict] = []
    for photo in photos:
        try:
            tech = compute_technical_scores(photo["path"], photo_id=photo["photo_id"])
            all_technical.append(tech)
        except ValueError as exc:
            logger.warning("technical scoring skipped a photo: %s", exc)
    logger.info("[perf] tech scoring: %.2fs", time.perf_counter() - _t)

    if not all_technical:
        raise _reject(422, "No images could be scored.")

    sharp_technical = [t for t in all_technical if t["blur_raw"] >= _BLUR_THRESHOLD]
    blurry_ids      = [t["photo_id"] for t in all_technical if t["blur_raw"] < _BLUR_THRESHOLD]

    # Gate thins the set before Gemini; it must not refuse the whole batch. If
    # nothing clears the threshold, score everything rather than rejecting.
    blur_gate_bypassed = not sharp_technical
    if blur_gate_bypassed:
        logger.warning(
            "set: all %d image(s) below blur threshold (%.1f); scoring all anyway",
            len(all_technical), _BLUR_THRESHOLD,
        )
        sharp_technical = all_technical

    _t = time.perf_counter()
    try:
        gemini_scores = score_photos(
            [t["path"] for t in sharp_technical],
            photo_ids=[t["photo_id"] for t in sharp_technical],
            profile=profile,
        )
    except EnvironmentError as exc:
        raise _reject(500, str(exc))
    except RuntimeError:
        # The exception text carries Gemini's raw reply, which describes the
        # photos. Never log or return it (privacy rule 2).
        raise _reject(502, f"Gemini scoring failed for {len(sharp_technical)} photo(s).")
    logger.info("[perf] gemini total: %.2fs (%d photos)", time.perf_counter() - _t, len(sharp_technical))

    # score_photos returns exactly one row per photo or raises. If one is still
    # missing, fail the request: never rank a set with a photo silently dropped.
    gemini_by_id = {s["photo_id"]: s for s in gemini_scores}
    missing = sum(1 for t in sharp_technical if t["photo_id"] not in gemini_by_id)
    if missing:
        raise _reject(502, f"Gemini returned no score for {missing} of {len(sharp_technical)} photo(s).")
    merged = [_merge(tech, gemini_by_id[tech["photo_id"]]) for tech in sharp_technical]

    if not merged:
        raise _reject(500, "Gemini returned no scoreable results.")

    ranked = rank_photos(merged, weights)
    return {
        "mode":               "set",
        "profile":            profile,
        "weights":            weights,
        "blur_threshold":     _BLUR_THRESHOLD,
        "blur_gate_bypassed": blur_gate_bypassed,
        "total_photos":       len(photos),
        "scored_photos":      len(merged),
        "skipped_blurry":     0 if blur_gate_bypassed else len(blurry_ids),
        "ranked":             ranked,
    }


# ---------------------------------------------------------------------------
# robots.txt — declared BEFORE the StaticFiles mount so the SPA fallback does
# not serve index.html for it (which caused Lighthouse SEO errors).
# ---------------------------------------------------------------------------

@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return "User-agent: *\nDisallow:\n"


# ---------------------------------------------------------------------------
# Static frontend (mounted LAST so /rank and /health take priority)
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class _SpaStaticFiles(StaticFiles):
    """
    Serve the built React PWA, falling back to index.html for any unmatched
    path so client-side routing (React Router) works on deep links / refresh.

    Everything outside assets/ (index.html, sw.js, the manifest, icons) is sent
    with Cache-Control: no-cache, so the browser revalidates it on every load
    (a cheap 304 via the ETag). Without it, Chrome caches index.html by
    heuristic for 10% of its age, and an installed PWA can keep loading the
    old shell for weeks after a deploy. Files in assets/ are named by content
    hash, so a new build never reuses their URLs.
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            response = FileResponse(self.directory / "index.html")
        if not path.startswith("assets/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


if _FRONTEND_DIST.is_dir():
    app.mount("/", _SpaStaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
else:
    logger.warning("frontend build not found at %s — static serving disabled", _FRONTEND_DIST)


# ---------------------------------------------------------------------------
# Dev entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8007, reload=True)
