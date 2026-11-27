"""
Build small JPEGs with real EXIF for tests: DateTimeOriginal in the Exif IFD
(where phones write it), and GPS coordinates in the GPS IFD, so tests can prove
GPS is never read or sent. The TIFF block is written by hand so both byte
orders ("II" little-endian, as Android writes; "MM" big-endian, as iPhone
writes) and odd layouts can be tested.
"""

import struct

ASCII, LONG, RATIONAL = 2, 4, 5
GPS_LATITUDE = (12, 58, 30)  # degrees, minutes, seconds: must never leave the phone


def _ascii(text: str) -> bytes:
    return text.encode("ascii") + b"\0"


def _tiff(dto: str | None, bo: str, gps: bool, dto_in_ifd0: bool,
          gps_pointer: int | None = None) -> bytes:
    """TIFF block: IFD0 -> Exif IFD (DateTimeOriginal) and, if gps, GPS IFD."""
    lat = b"".join(struct.pack(bo + "II", v, 1) for v in GPS_LATITUDE)
    ifd0 = [(0x010F, ASCII, _ascii("TestCam"))]
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
            count = len(payload) // 8 if typ == RATIONAL else len(payload)
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
         gps_pointer: int | None = None, blur: float = 0) -> bytes:
    """
    A noise JPEG; with `exif`, an EXIF APP1 right after SOI (after any padding/XMP).
    `blur` (a Gaussian sigma) softens it, so photos in a set score differently.
    """
    import cv2
    import numpy as np

    noise = np.random.default_rng(seed).integers(0, 256, (240, width, 3), dtype=np.uint8)
    if blur:
        noise = cv2.GaussianBlur(noise, (0, 0), blur)
    ok, buf = cv2.imencode(".jpg", noise)
    assert ok
    body = buf.tobytes()[2:]  # drop SOI
    head = b""
    for _ in range(pad_kb):  # APP2 padding, 1 KB each, to push EXIF further in
        head += _segment(0xE2, b"\0" * 1020)
    if xmp_first:
        head += _segment(0xE1, b"http://ns.adobe.com/xap/1.0/\0<x:xmpmeta/>")
    if exif:
        head += _segment(0xE1, b"Exif\0\0" + _tiff(dto, byteorder, gps, dto_in_ifd0, gps_pointer))
    return b"\xff\xd8" + head + body
