"""
The PWA's capture-time reader (frontend/src/lib/captureTime.js), run under Node.

It must return EXIF DateTimeOriginal from the first 128 KB of a JPEG and
nothing else: no other tag, never GPS. The same fixtures are checked against
the server's reader (core/group.py:read_capture_time), so both ends agree.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core.group import read_capture_time
from tests.exif_jpeg import jpeg

READER = Path(__file__).resolve().parent.parent / "frontend" / "src" / "lib" / "captureTime.js"
TIME = "2026:05:13 11:03:27"

# name -> (jpeg bytes, what the phone should read)
CASES = {
    "android_ii": (jpeg(dto=TIME, byteorder="<"), TIME),
    "iphone_mm": (jpeg(dto=TIME, byteorder=">"), TIME),
    "xmp_before_exif": (jpeg(dto=TIME, xmp_first=True), TIME),
    "no_gps": (jpeg(dto=TIME, gps=False), TIME),
    # A GPS pointer far outside the file: following it would fail the read, so
    # getting the time back proves the reader never touches the GPS IFD.
    "gps_pointer_unreadable": (jpeg(dto=TIME, gps_pointer=0x7FFFFF00), TIME),
    "no_exif": (jpeg(exif=False), None),
    "exif_without_time": (jpeg(dto=None), None),
    "blank_time": (jpeg(dto="    :  :     :  :  "), None),
    "time_only_in_ifd0": (jpeg(dto=TIME, dto_in_ifd0=True), None),
    "exif_past_128kb": (jpeg(dto=TIME, pad_kb=130), None),
    "truncated": (jpeg(dto=TIME)[:60], None),
    "not_a_jpeg": (b"\x89PNG\r\n\x1a\n" + b"\0" * 64, None),
}

NODE_SCRIPT = """
const { readCaptureTime } = await import(process.argv[1]);
const fs = await import("node:fs");
const files = JSON.parse(fs.readFileSync(0, "utf8"));
const out = {};
for (const [name, path] of Object.entries(files)) {
  out[name] = await readCaptureTime(new Blob([fs.readFileSync(path)]));
}
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def phone_reads(tmp_path_factory):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    tmp = tmp_path_factory.mktemp("exif")
    paths = {}
    for name, (data, _) in CASES.items():
        paths[name] = str(tmp / f"{name}.jpg")
        Path(paths[name]).write_bytes(data)
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, READER.as_uri()],
        input=json.dumps(paths), capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("name", CASES)
def test_phone_reads_only_date_time_original(phone_reads, name):
    assert phone_reads[name] == CASES[name][1]


@pytest.mark.parametrize("byteorder", ["<", ">"])
def test_server_reads_the_same_time_from_the_exif_ifd(tmp_path, byteorder):
    path = tmp_path / "photo.jpg"
    path.write_bytes(jpeg(dto=TIME, byteorder=byteorder))
    ms, source = read_capture_time(path)
    assert source == "exif_subsec"
    assert ms == 1778670207000 + 420  # 2026-05-13 11:03:27.42, as if UTC
