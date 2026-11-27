// Client-side image compression.
//
// Downscales each photo to ~1.5 MP and re-encodes as JPEG before upload. This
// keeps the request small and — critically for the preview — converts HEIC
// into something every browser can render. The same compressed blob is both
// uploaded (so the server's photo_id matches the original filename) and used
// for the on-screen thumbnail/hero, so what the user sees is what was scored.
//
// Decoding can fail. Android Chrome has rejected createImageBitmap for camera
// JPEGs that it displays fine (LEARNINGS 2026-11-27), so we then decode through
// an <img> instead. If the photo still can't be re-encoded, the original is
// NEVER uploaded as-is: it carries EXIF (GPS included), XMP, IPTC and more.
// A JPEG has every metadata segment cut out of its bytes, without decoding
// (lib/stripJpeg.js), and that is uploaded. Stripping drops the EXIF
// orientation, so the stored pixels may be sideways: the loading screen sends
// the orientation for these photos only (`orientations`), the server turns
// them upright before scoring, and the preview is the original, which this
// browser shows upright. Anything else (e.g. a HEIC this browser can't decode)
// is not uploaded at all; the loading and results screens name it.

import { COMPRESS_MAX_PIXELS, COMPRESS_QUALITY } from "./config.js";
import { stripJpegMetadata } from "./lib/stripJpeg.js";

/**
 * @param {File} file
 * @param {string} [name]  Name to upload under (default: the file's own name).
 *   The server returns it unchanged as the photo_id; see lib/uploadNames.js.
 * @returns {Promise<{ status: "compressed"|"stripped"|"unprepared",
 *                     file: File|null, previewUrl: string|null, name: string }>}
 *   `file` is what to upload, named `name`: the compressed JPEG, or the
 *   original JPEG with its metadata stripped; null when neither could be made
 *   ("unprepared": don't upload it). `previewUrl` is an object URL for
 *   display: of the compressed file, or of the original when stripped (it
 *   shows upright, as the server scores it; it never leaves the phone).
 *   `name` maps the result back to the preview by photo_id.
 */
export async function compressImage(file, name = file.name) {
  try {
    const blob = await reencode(file);
    return prepared("compressed", blob, name);
  } catch {
    const clean = await stripMetadata(file);
    if (!clean) return { status: "unprepared", file: null, previewUrl: null, name };
    return prepared("stripped", new Blob([clean], { type: "image/jpeg" }), name, file);
  }
}

// Upload under `name`: the server echoes it as the photo_id, which maps the
// result back to this preview on the results screen.
function prepared(status, blob, name, preview = blob) {
  const file = new File([blob], name, { type: "image/jpeg" });
  return { status, file, previewUrl: URL.createObjectURL(preview), name };
}

/** The file's bytes with all metadata removed, or null if it isn't a clean-able JPEG. */
async function stripMetadata(file) {
  try {
    return stripJpegMetadata(new Uint8Array(await file.arrayBuffer()));
  } catch {
    return null;
  }
}

/** Decode, downscale to ~1.5 MP and re-encode as JPEG. Throws on any failure. */
async function reencode(file) {
  const source = await decode(file);
  try {
    const { width, height } = source;
    const pixels = width * height;
    const scale = pixels > COMPRESS_MAX_PIXELS
      ? Math.sqrt(COMPRESS_MAX_PIXELS / pixels)
      : 1;
    const w = Math.max(1, Math.round(width * scale));
    const h = Math.max(1, Math.round(height * scale));

    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(source.image, 0, 0, w, h);

    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", COMPRESS_QUALITY),
    );
    if (!blob) throw new Error("toBlob returned null");
    return blob;
  } finally {
    source.release();
  }
}

/** {image, width, height, release} from createImageBitmap, else from an <img>. */
async function decode(file) {
  try {
    const bitmap = await createImageBitmap(file);
    return {
      image: bitmap, width: bitmap.width, height: bitmap.height,
      release: () => bitmap.close?.(),
    };
  } catch {
    // createImageBitmap can reject a photo that an <img> decodes (seen on
    // Android Chrome with camera JPEGs), so try that before giving up.
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      if (!img.naturalWidth || !img.naturalHeight) throw new Error("no size");
      return {
        image: img, width: img.naturalWidth, height: img.naturalHeight,
        release: () => URL.revokeObjectURL(url),
      };
    } catch (err) {
      URL.revokeObjectURL(url);
      throw err;
    }
  }
}
