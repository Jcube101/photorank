// PhotoRank API client. One call: POST /rank (multipart).
//
// Errors are translated into short, user-facing messages — raw server detail
// and stack traces are never surfaced to the UI (per the project's
// "graceful API error states" requirement).

import { API_BASE } from "./config.js";

export class RankError extends Error {
  constructor(message, { kind = "generic" } = {}) {
    super(message);
    this.name = "RankError";
    this.kind = kind; // "offline" | "client" | "server" | "network" | "generic"
  }
}

// Friendly copy keyed by HTTP status (SPECS §6.1).
function messageForStatus(status) {
  if (status === 422) {
    return {
      kind: "client",
      message: "Those photos couldn't be accepted. Use 2–20 images in JPEG, PNG, WebP, or HEIC.",
    };
  }
  if (status === 500) {
    return {
      kind: "server",
      message: "Scoring is temporarily unavailable. Please try again in a moment.",
    };
  }
  if (status === 502) {
    return {
      kind: "server",
      message: "The scoring engine didn't respond. Please try again in a moment.",
    };
  }
  return {
    kind: "server",
    message: "Something went wrong while ranking. Please try again.",
  };
}

/**
 * Rank a batch of photos.
 *
 * @param {File[]} files     Files to upload (already compressed). Field name: "files".
 * @param {string} profile   family | portrait | travel | event
 * @param {object} [opts]
 * @param {AbortSignal} [opts.signal]  Abort the request (e.g. user cancels).
 * @returns {Promise<object>} The ranker JSON (top-level output, SPECS §5.4).
 *
 * `mode` is intentionally omitted so the server auto-detects burst vs set
 * from EXIF timestamps (SPECS §0).
 */
export async function rankPhotos(files, profile, opts = {}) {
  if (!navigator.onLine) {
    throw new RankError(
      "You're offline. Connect to the internet to score your photos.",
      { kind: "offline" },
    );
  }

  const form = new FormData();
  for (const f of files) form.append("files", f, f.name);
  form.append("profile", profile);

  let res;
  try {
    res = await fetch(`${API_BASE}/rank`, {
      method: "POST",
      body: form,
      signal: opts.signal,
    });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    throw new RankError(
      "Couldn't reach PhotoRank. Check your connection and try again.",
      { kind: "network" },
    );
  }

  if (!res.ok) {
    const { kind, message } = messageForStatus(res.status);
    // Read and discard the body so we don't leak server detail to the user.
    await res.text().catch(() => {});
    throw new RankError(message, { kind });
  }

  let data;
  try {
    data = await res.json();
  } catch {
    throw new RankError("Got an unexpected response while ranking. Please try again.", {
      kind: "server",
    });
  }

  if (!data || !Array.isArray(data.ranked) || data.ranked.length === 0) {
    throw new RankError("No photos could be scored from that batch. Please try again.", {
      kind: "server",
    });
  }
  return data;
}
