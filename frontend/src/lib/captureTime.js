// Read a photo's capture time (EXIF DateTimeOriginal) on the phone.
//
// Canvas compression strips all EXIF, so the server cannot see when a photo
// was taken, and burst mode (≤ 6 photos within 10 s) could never trigger from
// the app. We read the time from the ORIGINAL file before compression and send
// it as a separate form field (SPECS §6.1 `capture_times`).
//
// Privacy: this reads exactly one tag, DateTimeOriginal (0x9003), and nothing
// else. It walks JPEG → APP1 "Exif" → IFD0 → the Exif-IFD pointer (0x8769) →
// 0x9003. It never follows the GPS pointer and never reads any other value.
//
// Only the first 128 KB is read (V2-PLAN §5.1): APP1/EXIF sits at the start of
// a JPEG, so the rest of a 10 MB photo never enters memory. JPEG only; HEIC and
// anything unreadable return null, and the server then falls back to set mode.

const SLICE_BYTES = 131072;
const TAG_EXIF_IFD = 0x8769;
const TAG_DATETIME_ORIGINAL = 0x9003;
const TYPE_ASCII = 2;
const EXIF_TIME = /^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$/;

/**
 * @param {Blob} file  The original, uncompressed photo.
 * @returns {Promise<string|null>} "YYYY:MM:DD HH:MM:SS", or null if absent.
 */
export async function readCaptureTime(file) {
  try {
    const buf = await file.slice(0, SLICE_BYTES).arrayBuffer();
    return parseCaptureTime(new DataView(buf));
  } catch {
    return null;
  }
}

/** DateTimeOriginal from the start of a JPEG, or null. Never throws. */
export function parseCaptureTime(view) {
  try {
    return findInJpeg(view);
  } catch {
    return null; // truncated or malformed: out-of-range reads throw RangeError
  }
}

function findInJpeg(view) {
  if (view.getUint16(0) !== 0xffd8) return null; // not a JPEG (SOI)
  let off = 2;
  while (off + 4 <= view.byteLength) {
    if (view.getUint8(off) !== 0xff) return null;
    const marker = view.getUint8(off + 1);
    if (marker === 0xff) {
      off += 1; // fill byte
      continue;
    }
    if (marker === 0xda || marker === 0xd9) return null; // image data / end: no EXIF
    const len = view.getUint16(off + 2);
    if (len < 2) return null;
    // APP1 can hold EXIF or XMP; only "Exif\0\0" is EXIF.
    if (marker === 0xe1 && len >= 8 && isExifHeader(view, off + 4)) {
      return readTiff(view, off + 10, Math.min(off + 2 + len, view.byteLength));
    }
    off += 2 + len;
  }
  return null;
}

function isExifHeader(view, at) {
  // "Exif" followed by two NUL bytes
  return view.getUint32(at) === 0x45786966 && view.getUint16(at + 4) === 0;
}

function readTiff(view, start, end) {
  const order = view.getUint16(start);
  const le = order === 0x4949 ? true : order === 0x4d4d ? false : null; // "II" / "MM"
  if (le === null) return null;
  const size = end - start;
  const u16 = (o) => {
    if (o < 0 || o + 2 > size) throw new RangeError("outside EXIF");
    return view.getUint16(start + o, le);
  };
  const u32 = (o) => {
    if (o < 0 || o + 4 > size) throw new RangeError("outside EXIF");
    return view.getUint32(start + o, le);
  };
  if (u16(2) !== 42) return null;

  // Offset of the 12-byte entry for `tag` in the IFD at `ifd`, or null.
  const entryFor = (ifd, tag) => {
    const count = u16(ifd);
    for (let i = 0; i < count; i++) {
      const entry = ifd + 2 + 12 * i;
      if (u16(entry) === tag) return entry;
    }
    return null;
  };

  const pointer = entryFor(u32(4), TAG_EXIF_IFD);
  if (pointer === null) return null;
  const dto = entryFor(u32(pointer + 8), TAG_DATETIME_ORIGINAL);
  if (dto === null || u16(dto + 2) !== TYPE_ASCII) return null;
  const length = u32(dto + 4);
  if (length < 19) return null;
  const at = length > 4 ? u32(dto + 8) : dto + 8;
  let text = "";
  for (let i = 0; i < 19; i++) {
    if (at + i >= size) return null;
    text += String.fromCharCode(view.getUint8(start + at + i));
  }
  return EXIF_TIME.test(text) ? text : null;
}
