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
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

load_dotenv()

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

# EXIF tag ID for DateTimeOriginal (0x9003)
_EXIF_DATETIME_ORIGINAL = 36867

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
# EXIF timestamp reading (burst auto-detection, runs before EXIF is stripped)
# ---------------------------------------------------------------------------

def _read_exif_timestamp(path: Path) -> Optional[datetime]:
    """
    Read DateTimeOriginal from raw uploaded file before it is stripped by ingest.
    Returns None if Pillow is unavailable, EXIF is absent, or parsing fails.
    Never logs file content.
    """
    try:
        from PIL import Image
        with Image.open(path) as img:
            exif = img.getexif()
        raw = exif.get(_EXIF_DATETIME_ORIGINAL)
        if raw:
            return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def _auto_detect_mode(upload_paths: list[Path]) -> str:
    """
    Return 'burst' when <=6 files all have timestamps within 10 s of each other.
    Falls back to 'set' if count exceeds limit or any timestamp is unreadable.
    """
    if len(upload_paths) > _BURST_MAX_PHOTOS:
        return "set"
    timestamps = [_read_exif_timestamp(p) for p in upload_paths]
    if any(ts is None for ts in timestamps):
        return "set"
    ts_sorted = sorted(timestamps)  # type: ignore[type-var]
    span = (ts_sorted[-1] - ts_sorted[0]).total_seconds()
    return "burst" if span <= _BURST_TIMESTAMP_WINDOW_S else "set"


# ---------------------------------------------------------------------------
# POST /rank
# ---------------------------------------------------------------------------

@app.post("/rank")
async def rank_endpoint(
    files: list[UploadFile] = File(...),
    profile: str = Form("family"),
    mode: Optional[str] = Form(None),
    weights: Optional[str] = Form(None),
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

    # (saved_path_on_disk, original_display_name)
    upload_info: list[tuple[Path, str]] = []
    seen_names: dict[str, int] = {}

    try:
        for upload in files:
            orig     = upload.filename or "image.jpg"
            stem     = Path(orig).stem
            suffix   = Path(orig).suffix.lower() or ".jpg"
            base     = f"{stem}{suffix}"
            count    = seen_names.get(base, 0)
            seen_names[base] = count + 1
            # Append _N suffix only when a name collision occurs
            display  = f"{stem}_{count}{suffix}" if count else base

            # Use uuid on disk so same-named uploads never collide in the dir
            dest = upload_dir / f"{uuid.uuid4().hex}{suffix}"
            dest.write_bytes(await upload.read())
            upload_info.append((dest, display))

        upload_paths   = [info[0] for info in upload_info]
        uuid_to_display = {p.name: name for p, name in upload_info}

        # --- Mode resolution (reads raw EXIF before ingest strips it) ---
        resolved_mode = mode if mode is not None else _auto_detect_mode(upload_paths)
        logger.info("/rank resolved mode=%s for %d file(s)", resolved_mode, len(upload_paths))

        # --- Ingest: cv2.imread + cv2.imwrite strips EXIF automatically ---
        _t_ingest = time.perf_counter()
        try:
            photos, ingest_temp_dir = ingest(upload_paths)
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
    except RuntimeError as exc:
        raise _reject(502, str(exc))
    logger.info("[perf] gemini total: %.2fs (%d photos)", time.perf_counter() - _t, len(sharp_technical))

    gemini_by_id = {s["photo_id"]: s for s in gemini_scores}
    merged: list[dict] = []
    for tech in sharp_technical:
        pid = tech["photo_id"]
        if pid not in gemini_by_id:
            logger.warning("no Gemini score for %s — skipping", pid)
            continue
        merged.append(_merge(tech, gemini_by_id[pid]))

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
# Static frontend (mounted LAST so /rank and /health take priority)
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


class _SpaStaticFiles(StaticFiles):
    """
    Serve the built React PWA, falling back to index.html for any unmatched
    path so client-side routing (React Router) works on deep links / refresh.
    """

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return FileResponse(self.directory / "index.html")
            raise


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
