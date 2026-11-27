"""
Build small JPEGs with real EXIF for tests: DateTimeOriginal in the Exif IFD
(where phones write it), and GPS coordinates in the GPS IFD, so tests can prove
GPS is never read or sent. The TIFF block is written by hand so both byte
orders ("II" little-endian, as Android writes; "MM" big-endian, as iPhone
writes) and odd layouts can be tested.
"""

import struct

ASCII, SHORT, LONG, RATIONAL = 2, 3, 4, 5
GPS_LATITUDE = (12, 58, 30)  # degrees, minutes, seconds: must never leave the phone

# Text planted in each other kind of metadata, so a test can search for it.
XMP_TEXT = b"<x:xmpmeta><exif:GPSLatitude>12,58.5N</exif:GPSLatitude></x:xmpmeta>"
IPTC_TEXT = b"Fort Kochi"
COMMENT_TEXT = b"taken at home"
TRAILER_TEXT = b"MotionPhoto_Data GPS 12.975"


def _ascii(text: str) -> bytes:
    return text.encode("ascii") + b"\0"


def _tiff(dto: str | None, bo: str, gps: bool, dto_in_ifd0: bool,
          gps_pointer: int | None = None, orientation: int | None = None) -> bytes:
    """TIFF block: IFD0 (Orientation) -> Exif IFD (DateTimeOriginal) and, if gps, GPS IFD."""
    lat = b"".join(struct.pack(bo + "II", v, 1) for v in GPS_LATITUDE)
    ifd0 = [(0x010F, ASCII, _ascii("TestCam"))]
    if orientation is not None:
        ifd0.append((0x0112, SHORT, struct.pack(bo + "H", orientation)))
    exif = [(0x9291, ASCII, _ascii("42"))]  # SubSecTimeOriginal
    if dto is not None:
        (ifd0 if dto_in_ifd0 else exif).append((0x9003, ASCII, _ascii(dto)))
    gps_ifd = [(0x0001, ASCII, _ascii("N")), (0x0002, RATIONAL, lat)]

    ifds = [ifd0, exif] + ([gps_ifd] if gps else [])
    ifd0.append((0x8769, LONG, None))  # pointers, filled in below
    if gps:
        ifd0.append((0x8825, LONG, None))
    sizes = [2 + 12 * len(entries) + 4 for entries in ifds]
    offsets = [8]
    for size in sizes[:-1]:
        offsets.append(offsets[-1] + size)
    pointer = {0x8769: offsets[1], 0x8825: offsets[2] if gps else 0}
    if gps_pointer is not None:  # e.g. far outside the block, to prove nobody follows it
        pointer[0x8825] = gps_pointer

    data_at = offsets[-1] + sizes[-1]
    blocks, data = [], b""
    for entries in ifds:
        out = struct.pack(bo + "H", len(entries))
        for tag, typ, payload in sorted(entries, key=lambda e: e[0]):
            if payload is None:
                out += struct.pack(bo + "HHII", tag, LONG, 1, pointer[tag])
                continue
            count = {RATIONAL: len(payload) // 8, SHORT: len(payload) // 2}.get(typ, len(payload))
            if len(payload) <= 4:
                out += struct.pack(bo + "HHI", tag, typ, count) + payload.ljust(4, b"\0")
            else:
                out += struct.pack(bo + "HHII", tag, typ, count, data_at + len(data))
                data += payload + (b"\0" if len(payload) % 2 else b"")
        blocks.append(out + struct.pack(bo + "I", 0))
    header = (b"II" if bo == "<" else b"MM") + struct.pack(bo + "HI", 42, 8)
    return header + b"".join(blocks) + data


def _segment(marker: int, body: bytes) -> bytes:
    return struct.pack(">BBH", 0xFF, marker, len(body) + 2) + body


def jpeg(width: int = 320, seed: int = 0, dto: str | None = None, *, byteorder: str = "<",
         gps: bool = True, xmp_first: bool = False, pad_kb: int = 0,
         dto_in_ifd0: bool = False, exif: bool = True,
         gps_pointer: int | None = None, blur: float = 0,
         all_metadata: bool = False, adobe: bool = False,
         progressive: bool = False, restart: int = 0, orientation: int | None = None,
         height: int = 240) -> bytes:
    """
    A noise JPEG; with `exif`, an EXIF APP1 right after SOI (after any padding/XMP).
    `blur` (a Gaussian sigma) softens it, so photos in a set score differently.
    `all_metadata` adds every other kind a phone photo can carry, each holding
    planted text: XMP (APP1), an ICC profile (APP2), IPTC (APP13), a comment
    (COM), and a trailer after the end of the image, as Motion Photos have.
    `adobe` adds a standard Adobe APP14; `progressive` and `restart` (a restart
    interval in MCUs) change how the image data is coded. `orientation` adds
    the EXIF Orientation tag (1-8) to IFD0; the pixels are stored as given.
    """
    import cv2
    import numpy as np

    noise = np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)
    if blur:
        noise = cv2.GaussianBlur(noise, (0, 0), blur)
    params = []
    if progressive:
        params += [cv2.IMWRITE_JPEG_PROGRESSIVE, 1]
    if restart:
        params += [cv2.IMWRITE_JPEG_RST_INTERVAL, restart]
    ok, buf = cv2.imencode(".jpg", noise, params)
    assert ok
    body = buf.tobytes()[2:]  # drop SOI
    head = b""
    for _ in range(pad_kb):  # APP2 padding, 1 KB each, to push EXIF further in
        head += _segment(0xE2, b"\0" * 1020)
    if xmp_first:
        head += _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta/>")
    if exif:
        head += _segment(0xE1, b"Exif\0\0" + _tiff(dto, byteorder, gps, dto_in_ifd0, gps_pointer,
                                                    orientation))
    tail = b""
    if all_metadata:
        head += _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0" + XMP_TEXT)
        head += _segment(0xE2, b"ICC_PROFILE\0\x01\x01" + b"\0" * 128)
        head += _segment(0xED, b"Photoshop 3.0\08BIM\x04\x04\0\0\0\0\0\x0e\x1c\x02\x5a\0\x0a" + IPTC_TEXT)
        head += _segment(0xFE, COMMENT_TEXT)
        tail = TRAILER_TEXT
    if adobe:
        head += _segment(0xEE, b"Adobe\0\x64\0\0\0\0\x01")
    return b"\xff\xd8" + head + body + tail


def markers(data: bytes) -> list[int]:
    """The marker of every segment before the image data ends (0xD9 = EOI)."""
    found, i = [], 2
    while i + 2 <= len(data):
        assert data[i] == 0xFF, f"no marker at {i}"
        m = data[i + 1]
        found.append(m)
        if m == 0xD9:
            break
        assert i + 4 <= len(data), f"truncated segment at {i}"
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
        if m == 0xDA:  # entropy-coded data runs to the next marker that is not RSTn/stuffing
            while i + 1 < len(data) and not (data[i] == 0xFF and data[i + 1] not in (0x00, *range(0xD0, 0xD8))):
                i += 1
    return found
