// Remove every metadata segment from a JPEG, without decoding it.
//
// Used when compression fails (compress.js), so an original never leaves the
// phone as-is: it carries EXIF (GPS included), XMP, IPTC, comments and more.
// It keeps an allowlist, only what a decoder needs, so metadata we have not
// heard of is dropped too:
//   SOI; SOFn (frame); DHT, DAC (entropy tables); DQT (quantisation);
//   DRI (restart interval); SOS and its entropy-coded data; DNL; EOI; and
//   APP14 only in its standard 12-byte "Adobe" form (the colour transform,
//   needed to decode some JPEGs; nothing personal).
// Dropped: every other APPn (JFIF and its thumbnail; EXIF and XMP in APP1;
// ICC and MPF in APP2; IPTC in APP13; …), COM comments, any other marker,
// and everything after the first EOI (Ultra HDR gain maps, Samsung trailers,
// Motion Photo videos).
//
// Orientation lives in EXIF, so a rotated photo arrives unrotated.
//
// No imports, so it runs under Node (tests/test_strip_jpeg.py).

const KEEP = new Set([
  0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf, // SOFn
  0xc4, // DHT
  0xcc, // DAC
  0xdb, // DQT
  0xdd, // DRI
  0xdc, // DNL
]);
const SOS = 0xda;
const EOI = 0xd9;
const APP14 = 0xee;
const ADOBE = [0x41, 0x64, 0x6f, 0x62, 0x65]; // "Adobe"

/**
 * @param {Uint8Array} bytes  A whole JPEG file.
 * @returns {Uint8Array|null}  The image with no metadata, or null if this is
 *   not a JPEG that can be cleaned this way (not a JPEG, truncated, malformed).
 */
export function stripJpegMetadata(bytes) {
  try {
    return strip(bytes);
  } catch {
    return null;
  }
}

function strip(b) {
  if (b.length < 4 || b[0] !== 0xff || b[1] !== 0xd8) return null; // SOI
  const parts = [b.subarray(0, 2)];
  let frame = false;
  let scan = false;
  let i = 2;
  while (i + 1 < b.length) {
    if (b[i] !== 0xff) return null;
    const m = b[i + 1];
    if (m === 0xff) {
      i += 1; // fill byte
      continue;
    }
    if (m === EOI) {
      if (!scan) return null;
      parts.push(b.subarray(i, i + 2)); // and nothing after it
      return concat(parts);
    }
    if (i + 4 > b.length) return null;
    const len = (b[i + 2] << 8) | b[i + 3];
    if (len < 2 || i + 2 + len > b.length) return null;
    const segment = b.subarray(i, i + 2 + len);
    i += 2 + len;

    if (KEEP.has(m)) {
      if (m !== 0xc4 && m !== 0xcc && m >= 0xc0 && m <= 0xcf) frame = true;
      parts.push(segment);
    } else if (m === APP14 && isAdobe(segment)) {
      parts.push(segment);
    } else if (m === SOS) {
      if (!frame) return null;
      scan = true;
      parts.push(segment);
      // Entropy-coded data runs to the next marker: 0xFF followed by anything
      // but 0x00 (a stuffed byte) or RST0–7.
      const start = i;
      while (i + 1 < b.length) {
        if (b[i] === 0xff) {
          const n = b[i + 1];
          if (n === 0x00 || (n >= 0xd0 && n <= 0xd7)) {
            i += 2;
            continue;
          }
          break;
        }
        i += 1;
      }
      parts.push(b.subarray(start, i));
    }
    // Anything else (APPn, COM, reserved markers) is dropped.
  }
  return null; // no EOI: truncated
}

// The standard Adobe APP14: "Adobe", version, flags0, flags1, transform.
function isAdobe(segment) {
  if (segment.length !== 16) return false; // marker (2) + length (2) + 12
  return ADOBE.every((c, k) => segment[4 + k] === c);
}

function concat(parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}
