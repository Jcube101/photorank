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
// an <img> instead. If the photo still can't be re-encoded, we upload the
// original untouched and preview the original: the results card shows it if
// this browser can display it, and the gradient placeholder if not (e.g. HEIC
// outside Safari).

import { COMPRESS_MAX_PIXELS, COMPRESS_QUALITY } from "./config.js";

/**
 * @param {File} file
 * @param {string} [name]  Name to upload under (default: the file's own name).
 *   The server returns it unchanged as the photo_id; see lib/uploadNames.js.
 * @returns {Promise<{ file: File, previewUrl: string, name: string, compressed: boolean }>}
 *   `file` is the blob to upload (compressed when possible, else the original),
 *   named `name`; `previewUrl` is an object URL for display: of the compressed
 *   blob, or of the original when compression failed (it may not be
 *   displayable; the cards fall back to a placeholder). `name` maps the result
 *   back to the preview by photo_id.
 */
export async function compressImage(file, name = file.name) {
  try {
    const blob = await reencode(file);
    // Upload under `name`: the server echoes it as the photo_id, which maps
    // the result back to this preview on the results screen.
    const out = new File([blob], name, { type: "image/jpeg" });
    return { file: out, previewUrl: URL.createObjectURL(blob), name, compressed: true };
  } catch {
    // Upload the original so scoring still works, and preview it as it is.
    const out = name === file.name ? file : new File([file], name, { type: file.type });
    return { file: out, previewUrl: URL.createObjectURL(file), name, compressed: false };
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
