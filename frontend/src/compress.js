// Client-side image compression.
//
// Downscales each photo to ~1.5 MP and re-encodes as JPEG before upload. This
// keeps the request small and — critically for the preview — converts HEIC
// into something every browser can render. The same compressed blob is both
// uploaded (so the server's photo_id matches the original filename) and used
// for the on-screen thumbnail/hero, so what the user sees is what was scored.
//
// Decoding can fail (e.g. HEIC on a browser without a decoder — typically only
// iOS Safari can). In that case we fall back to sending the original file
// untouched, and signal that no displayable preview is available so the UI
// shows the design's gradient placeholder instead.

import { COMPRESS_MAX_PIXELS, COMPRESS_QUALITY } from "./config.js";

/**
 * @param {File} file
 * @returns {Promise<{ file: File, previewUrl: string|null, name: string }>}
 *   `file` is the blob to upload (compressed when possible, else the original);
 *   `previewUrl` is an object URL for display, or null when decode failed.
 *   `name` is the original filename — used to map results back by photo_id.
 */
export async function compressImage(file) {
  const name = file.name;
  try {
    const bitmap = await createImageBitmap(file);
    try {
      const { width, height } = bitmap;
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
      ctx.drawImage(bitmap, 0, 0, w, h);

      const blob = await new Promise((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", COMPRESS_QUALITY),
      );
      if (!blob) throw new Error("toBlob returned null");

      // Keep the original filename so the server's photo_id (the upload
      // filename) maps back to this file's preview on the results screen.
      const out = new File([blob], name, { type: "image/jpeg" });
      const previewUrl = URL.createObjectURL(blob);
      return { file: out, previewUrl, name };
    } finally {
      bitmap.close?.();
    }
  } catch {
    // Could not decode (unsupported HEIC on this browser, corrupt file, …).
    // Upload the original so scoring still works; no preview is available.
    return { file, previewUrl: null, name };
  }
}

/** Compress a list of files in sequence (keeps peak memory bounded on mobile). */
export async function compressAll(files) {
  const out = [];
  for (const f of files) {
    out.push(await compressImage(f));
  }
  return out;
}
