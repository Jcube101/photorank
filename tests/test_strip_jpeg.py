"""
The PWA's JPEG metadata stripper (frontend/src/lib/stripJpeg.js), run under Node.

When compression fails, the phone uploads the original JPEG with every
metadata segment cut out of its bytes (no decoding): no EXIF (so no GPS), XMP,
ICC, IPTC, comments, or anything after the image ends. The pixels must be
untouched. Anything it can't clean that way returns null and is not uploaded.
"""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.exif_jpeg import (
    COMMENT_TEXT,
    GPS_LATITUDE,
    IPTC_TEXT,
    TRAILER_TEXT,
    XMP_TEXT,
    jpeg,
    markers,
)

STRIPPER = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "stripJpeg.js"
TIME = "2026:05:13 11:03:27"

NODE_SCRIPT = """
const { stripJpegMetadata } = await import(process.argv[1]);
const fs = await import("node:fs");
const files = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};
for (const [name, b64] of Object.entries(files)) {
  const clean = stripJpegMetadata(new Uint8Array(Buffer.from(b64, "base64")));
  out[name] = clean === null ? null : Buffer.from(clean).toString("base64");
}
console.log(JSON.stringify(out));
"""

# Everything a decoder needs; nothing else may survive.
ALLOWED = {0xD8, 0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xCC, 0xDB, 0xDD, 0xDA, 0xD9, 0xEE}

CLEANABLE = {
    "phone_photo": jpeg(dto=TIME, all_metadata=True),
    "iphone_mm": jpeg(dto=TIME, byteorder=">", all_metadata=True),
    "xmp_first": jpeg(dto=TIME, xmp_first=True, all_metadata=True),
    "progressive": jpeg(dto=TIME, all_metadata=True, progressive=True),
    "restart_markers": jpeg(dto=TIME, all_metadata=True, restart=4),
    "adobe": jpeg(dto=TIME, all_metadata=True, adobe=True),
    "no_metadata": jpeg(exif=False),
}
NOT_CLEANABLE = {
    "heic": b"\0\0\0\x18ftypheic\0\0\0\0mif1heic" + b"\0" * 64,
    "png": b"\x89PNG\r\n\x1a\n" + b"\0" * 64,
    "truncated": jpeg(dto=TIME, all_metadata=True)[:4000],
    "empty": b"",
}


def _strip(files: dict[str, bytes]) -> dict[str, bytes | None]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, str(STRIPPER)],
        input=json.dumps({k: base64.b64encode(v).decode() for k, v in files.items()}),
        capture_output=True, text=True, check=True, timeout=60,
    )
    return {k: None if v is None else base64.b64decode(v) for k, v in json.loads(proc.stdout).items()}


@pytest.fixture(scope="module")
def stripped():
    return _strip({**CLEANABLE, **NOT_CLEANABLE})


def _pixels(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    assert img is not None
    return img


def _gps_bytes(byteorder: str) -> list[bytes]:
    import struct
    return [struct.pack(byteorder + "II", v, 1) for v in GPS_LATITUDE]


def test_the_fixture_carries_every_kind_of_metadata():
    original = CLEANABLE["phone_photo"]
    for planted in (b"Exif", XMP_TEXT, b"ICC_PROFILE", IPTC_TEXT, COMMENT_TEXT, TRAILER_TEXT,
                    TIME.encode(), *_gps_bytes("<")):
        assert planted in original, planted
    assert {0xE1, 0xE2, 0xED, 0xFE} <= set(markers(original))


@pytest.mark.parametrize("name", CLEANABLE)
def test_no_metadata_survives(stripped, name):
    out = stripped[name]
    assert out is not None
    assert set(markers(out)) <= ALLOWED, [hex(m) for m in markers(out)]
    assert out.endswith(b"\xff\xd9")  # nothing after the image
    for planted in (b"Exif", b"http://ns.adobe.com/xap", XMP_TEXT, b"ICC_PROFILE", b"Photoshop",
                    IPTC_TEXT, COMMENT_TEXT, TRAILER_TEXT, TIME.encode(), b"TestCam",
                    *_gps_bytes("<"), *_gps_bytes(">")):
        assert planted not in out, planted


@pytest.mark.parametrize("name", CLEANABLE)
def test_pixels_are_untouched(stripped, name):
    assert np.array_equal(_pixels(stripped[name]), _pixels(CLEANABLE[name]))


def test_the_standard_adobe_segment_is_kept(stripped):
    # It sets the colour transform, which some JPEGs need to decode correctly.
    assert 0xEE in markers(stripped["adobe"])
    assert 0xEE not in markers(stripped["phone_photo"])


@pytest.mark.parametrize("name", NOT_CLEANABLE)
def test_what_cannot_be_cleaned_is_refused(stripped, name):
    assert stripped[name] is None
