"""
Image ingestion: collect, validate formats, compress to ~1.5 MP, assign photo IDs.

Input:  a directory, a single file, or a list of paths
Output: list of photo dicts + a temp directory path

Each photo dict:
  photo_id:      str — original filename, used as the stable ID throughout the pipeline
  original_path: str — path to the source file
  path:          str — path to the compressed JPEG in temp dir (used for scoring)

Compression target: 1,500,000 pixels (~1.5 MP).
At 4:3 this is roughly 1414×1060. Keeps Gemini API payloads small and
MediaPipe processing fast on a Raspberry Pi.

Caller must call cleanup(temp_dir) when done — this satisfies the privacy
requirement that no photo data outlives the scoring session.
"""

import shutil
import tempfile
from pathlib import Path

import cv2

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
_TARGET_PIXELS = 1_500_000
_JPEG_QUALITY  = 85


def collect_images(source: str | Path) -> list[Path]:
    """
    Return supported image paths from a directory (non-recursive),
    or wrap a single valid file in a list.
    """
    src = Path(source)
    if src.is_file():
        if src.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported format: {src.suffix}")
        return [src]
    if not src.is_dir():
        raise ValueError(f"Not a file or directory: {src}")
    return sorted(p for p in src.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS)


def _compress(src: Path, dest_dir: Path) -> Path:
    """
    Load src, resize down to _TARGET_PIXELS if larger, write JPEG to dest_dir.
    Keeps aspect ratio. Returns the destination path.
    """
    bgr = cv2.imread(str(src))
    if bgr is None:
        raise ValueError(f"Could not read image: {src}")

    h, w = bgr.shape[:2]
    if h * w > _TARGET_PIXELS:
        scale = (_TARGET_PIXELS / (h * w)) ** 0.5
        bgr   = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)

    dest = dest_dir / (src.stem + ".jpg")
    cv2.imwrite(str(dest), bgr, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    return dest


def ingest(source: str | Path | list[str | Path]) -> tuple[list[dict], Path]:
    """
    Collect, validate, and compress images for the scoring pipeline.

    Args:
        source: a directory path, a single image path, or a list of paths.

    Returns:
        (photos, temp_dir)
          photos   — list of {photo_id, original_path, path} dicts, one per image
          temp_dir — Path to the temp directory holding compressed files;
                     caller must call cleanup(temp_dir) when done

    Raises:
        ValueError: source contains no supported images, or all images fail to load.
    """
    paths: list[Path]
    if isinstance(source, list):
        paths = [Path(p) for p in source]
    else:
        paths = collect_images(source)

    if not paths:
        raise ValueError(f"No supported images found in: {source}")

    temp_dir = Path(tempfile.mkdtemp(prefix="photorank_"))
    photos: list[dict] = []
    skipped = 0

    for p in paths:
        try:
            compressed = _compress(p, temp_dir)
            photos.append({
                "photo_id":      p.name,
                "original_path": str(p),
                "path":          str(compressed),
            })
        except ValueError as e:
            print(f"  [ingest] skipping {p.name}: {e}")
            skipped += 1

    if skipped:
        print(f"  [ingest] {skipped} file(s) skipped (unreadable)")

    if not photos:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise ValueError("No images could be loaded from the source.")

    return photos, temp_dir


def cleanup(temp_dir: str | Path) -> None:
    """Delete the temp directory created by ingest(). Always call this when done."""
    shutil.rmtree(str(temp_dir), ignore_errors=True)


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python ingest.py <directory_or_file>")
        sys.exit(1)

    photos, tmp = ingest(sys.argv[1])
    try:
        summary = [
            {"photo_id": p["photo_id"], "original_path": p["original_path"]}
            for p in photos
        ]
        print(json.dumps(summary, indent=2))
        print(f"\n{len(photos)} image(s) ingested.", file=sys.stderr)
    finally:
        cleanup(tmp)
