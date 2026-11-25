"""
Build a labelling page for one shoot, so the answer key (eval/README.md) is
made by correcting the engine's proposed sets instead of labelling from
scratch.

    PYTHONPATH=. ./venv/bin/python scripts/label_shoot.py \\
        --input input/wat_arun --out output/labeller/

Writes to --out (keep it under the git-ignored output/):
  index.html   one self-contained page: CSS, JS and data inline, no network
  thumbs/      ~400 px JPEGs, orientation applied, no EXIF
  large/       ~1600 px JPEGs for the full-screen viewer (--large 0 skips them)
  images.json  which images are current, so a re-run only redoes changed ones

Then serve it on this machine only:

    python -m http.server 8765 --bind 127.0.0.1 --directory output/labeller

No Gemini calls. The page holds photo names, capture-time gaps and the
proposed boundaries with their reasons. It holds no scores or rankings, so
picks are not biased by the model. The page logic is scripts/labeller/core.js,
which tests/test_label_shoot.py runs under Node.

Grouping features are read from, and saved to, the evaluation cache (default
output/eval_<shoot>_scores.json), so scripts/eval_shoot.py does not
fingerprint the shoot again when the key's "shoot" matches.
"""

import argparse
import hashlib
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageOps

from core.group import group_photos
from core.ingest import collect_images
from scripts.eval_shoot import Cache, load_features

ASSETS        = Path(__file__).parent / "labeller"
PAGE_VERSION  = 1
THUMB_QUALITY = 80
LARGE_QUALITY = 85
IMAGE_WORKERS = 4


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def make_image(src: str | Path, dest: str | Path, long_side: int, quality: int) -> tuple[int, int]:
    """
    Write a JPEG copy of src whose longest side is at most long_side pixels.

    EXIF orientation is applied to the pixels, and no metadata is written, so
    the copy carries no EXIF (and no GPS). Returns the written (width, height).
    """
    dest = Path(dest)
    with Image.open(src) as im:
        w, h = im.size
        scale = min(1.0, long_side / max(w, h))
        # JPEG only: decode at the smallest DCT scale that still covers the
        # target, instead of decoding all 24 MP first.
        im.draft("RGB", (max(1, round(w * scale)), max(1, round(h * scale))))
        img = ImageOps.exif_transpose(im).convert("RGB")
    img.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    img.save(tmp, "JPEG", quality=quality, optimize=True)
    tmp.replace(dest)
    return img.size


def build_images(
    paths: list[Path], out_dir: Path, long_side: int, quality: int, manifest: dict
) -> tuple[dict[str, tuple[int, int]], int]:
    """
    One JPEG per photo in out_dir, named <stem>.jpg. Re-uses an image when its
    source file and size setting are unchanged since it was written. Deletes
    images this function wrote earlier for photos no longer in the shoot.

    `manifest` ({stem: {"sig", "size"}}) is updated in place.
    Returns ({stem: (width, height)}, number of images written).
    """
    def signature(p: Path) -> list[int]:
        stat = p.stat()
        return [stat.st_size, stat.st_mtime_ns, long_side]

    todo = [
        p for p in paths
        if manifest.get(p.stem, {}).get("sig") != signature(p) or not (out_dir / f"{p.stem}.jpg").exists()
    ]
    if todo:
        with ThreadPoolExecutor(max_workers=IMAGE_WORKERS) as pool:
            sizes = list(pool.map(
                lambda p: make_image(p, out_dir / f"{p.stem}.jpg", long_side, quality), todo
            ))
        for p, size in zip(todo, sizes, strict=True):
            manifest[p.stem] = {"sig": signature(p), "size": list(size)}

    current = {p.stem for p in paths}
    for stem in sorted(set(manifest) - current):
        (out_dir / f"{stem}.jpg").unlink(missing_ok=True)
        del manifest[stem]
    return {p.stem: tuple(manifest[p.stem]["size"]) for p in paths}, len(todo)


def remove_images(out_dir: Path, manifest: dict) -> None:
    """Delete the images recorded in manifest (and nothing else)."""
    for stem in list(manifest):
        (out_dir / f"{stem}.jpg").unlink(missing_ok=True)
        del manifest[stem]


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def _digest(items: list[str]) -> str:
    return hashlib.sha256("|".join(items).encode()).hexdigest()[:12]


def page_data(
    grouping: dict,
    features: list[dict],
    shoot: str,
    camera: str | None,
    thumb_sizes: dict[str, tuple[int, int]],
    has_large: bool,
    generated: str = "",
) -> dict:
    """
    Everything the page needs, from group_photos() output.

    Holds photo names, capture time of day, the gap to the previous photo,
    and the proposed boundaries with their reasons and distances. Holds no
    scores or rankings.
    """
    by_id   = {f["photo_id"]: f for f in features}
    ordered = [pid for g in grouping["groups"] for pid in g["photo_ids"]]
    gaps    = [None] + [p["dt"] for p in grouping["pairs"]]

    photos = []
    for pid, gap in zip(ordered, gaps, strict=True):
        stem = Path(pid).stem
        w, h = thumb_sizes[stem]
        ms   = by_id[pid]["captured_at_ms"]
        photos.append({
            "id":    pid,
            "stem":  stem,
            "thumb": f"thumbs/{quote(stem)}.jpg",
            "large": f"large/{quote(stem)}.jpg" if has_large else None,
            "w":     w,
            "h":     h,
            # Capture times are parsed as if UTC, so this is the camera's clock.
            "time":  None if ms is None else datetime.fromtimestamp(ms / 1000, UTC).strftime("%H:%M:%S"),
            "gap_s": gap,
        })

    proposal = []
    for group in grouping["groups"]:
        b = group["boundary_before"]
        proposal.append({
            "start":  group["photo_ids"][0],
            "reason": b["reason"],
            "dt_s":   b.get("dt_s"),
            "dh":     b.get("dh"),
            "dc":     b.get("dc"),
        })

    return {
        "page_version":     PAGE_VERSION,
        "shoot":            shoot,
        "camera":           camera,
        "generated":        generated,
        "order":            grouping["order"],
        "grouping_version": grouping["grouping_version"],
        # Saved progress is keyed on the photo list; a changed proposal is
        # flagged to the labeller but keeps their edits.
        "photo_hash":       _digest(ordered),
        "proposal_hash":    _digest([p["start"] for p in proposal]),
        "photos":           photos,
        "proposal":         proposal,
    }


def _script_safe_json(data: dict) -> str:
    """JSON that cannot close or confuse the <script> element it sits in."""
    text = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def render_html(data: dict) -> str:
    """The self-contained page: template with CSS, JS and data inlined."""
    scripts = {name: (ASSETS / name).read_text() for name in ("core.js", "app.js")}
    for name, text in scripts.items():
        if "</script" in text.lower():
            raise ValueError(f"{name} contains '</script', which would end the inline script early")

    values = {
        "/*__CSS__*/":          (ASSETS / "style.css").read_text(),
        "/*__CORE__*/":         scripts["core.js"],
        "/*__APP__*/":          scripts["app.js"],
        "__DATA__":             _script_safe_json(data),
        "__TITLE__":            html.escape(data["shoot"]),
        "__SHOOT_PATH__":       html.escape(data["shoot"]),
        "__GENERATED__":        html.escape(data["generated"]),
        "__GROUPING_VERSION__": html.escape(str(data["grouping_version"])),
    }
    # One pass, so text inserted for one placeholder is never scanned for another.
    pattern = re.compile("|".join(re.escape(k) for k in values))
    return pattern.sub(lambda m: values[m.group(0)], (ASSETS / "template.html").read_text())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a page for correcting a shoot's proposed sets into an answer key"
    )
    parser.add_argument("--input", "-i", required=True, help="Folder of the shoot's photos")
    parser.add_argument("--out", "-o", default="output/labeller",
                        help="Output folder (default output/labeller)")
    parser.add_argument("--shoot", default=None,
                        help="Shoot name written into the key (default: the input folder's name)")
    parser.add_argument("--camera", default=None, help="Free-text camera note for the key (optional)")
    parser.add_argument("--cache", default=None,
                        help="Feature cache (default output/eval_<shoot>_scores.json, shared with eval_shoot.py)")
    parser.add_argument("--thumb", type=int, default=400,
                        help="Thumbnail longest side in pixels (default 400)")
    parser.add_argument("--large", type=int, default=1600,
                        help="Viewer image longest side in pixels; 0 skips them (default 1600)")
    args = parser.parse_args(argv)
    if args.thumb < 32:
        parser.error("--thumb must be at least 32")
    if args.large and args.large < args.thumb:
        parser.error("--large must be 0 or at least --thumb")

    shoot = args.shoot or Path(args.input).resolve().name
    out   = Path(args.out)
    try:
        paths = collect_images(args.input)
        if not paths:
            raise ValueError(f"No supported images found in: {args.input}")
        stems = [p.stem for p in paths]
        clashes = sorted({s for s in stems if stems.count(s) > 1})
        if clashes:
            raise ValueError(f"Photos share a name without extension, so a key cannot tell them apart: {clashes[:5]}")
        cache    = Cache(args.cache or f"output/eval_{shoot}_scores.json")
        features = load_features(args.input, cache)
    except (ValueError, OSError) as e:
        print(f"[label] error: {e}", file=sys.stderr)
        return 1

    grouping = group_photos(features)

    manifest_path = out / "images.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    print(f"[label] writing images for {len(paths)} photo(s)...", file=sys.stderr)
    thumbs, thumbs_written = build_images(
        paths, out / "thumbs", args.thumb, THUMB_QUALITY, manifest.setdefault("thumbs", {})
    )
    large_written = 0
    if args.large:
        _, large_written = build_images(
            paths, out / "large", args.large, LARGE_QUALITY, manifest.setdefault("large", {})
        )
    else:
        remove_images(out / "large", manifest.setdefault("large", {}))
    manifest_path.write_text(json.dumps(manifest))

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    data = page_data(grouping, features, shoot, args.camera, thumbs, bool(args.large), generated)
    page = out / "index.html"
    page.write_text(render_html(data))

    sources = ", ".join(sorted({f["time_source"] for f in features}))
    print(
        f"[label] {len(paths)} photos, {len(grouping['groups'])} proposed sets "
        f"(order: {grouping['order']}; time sources: {sources})",
        file=sys.stderr,
    )
    print(
        f"[label] images written: {thumbs_written} thumbnail(s), {large_written} viewer image(s); "
        "the rest were current",
        file=sys.stderr,
    )
    print(f"[label] page: {page}", file=sys.stderr)
    print(
        "[label] serve it on this machine only:\n"
        f"          python -m http.server 8765 --bind 127.0.0.1 --directory {out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
