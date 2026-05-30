// PhotoRank PWA — shared configuration and constants.

// Production API. Override at build time with VITE_API_BASE if pointing at a
// local FastAPI instance (e.g. http://localhost:8007).
export const API_BASE =
  import.meta.env.VITE_API_BASE || "https://photorank.job-joseph.com";

// File-count contract from the API (SPECS §6.1): 2–20 images per batch.
export const MIN_FILES = 2;
export const MAX_FILES = 20;

// Accepted formats (SPECS §1). HEIC included for iPhone.
export const ACCEPTED_MIME = [
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/heic",
  "image/heif",
];

// Client-side compression target. Roughly 1.5 MP (≈ the server's ingest target),
// keeping uploads small without throwing away the detail the scorer needs.
export const COMPRESS_MAX_PIXELS = 1_500_000;
export const COMPRESS_QUALITY = 0.85;

// Scoring profiles. ids match the API (family / portrait / travel / event);
// copy is taken from the approved design (frontend/_design/App.jsx).
export const PROFILES = [
  { id: "family", name: "Family", desc: "Everyone smiling, eyes open" },
  { id: "portrait", name: "Portrait", desc: "Sharp face, soft background" },
  { id: "travel", name: "Travel", desc: "Composition & exposure first" },
  { id: "event", name: "Event", desc: "Moment, energy, light" },
];

export function profileName(id) {
  return PROFILES.find((p) => p.id === id)?.name || "Custom";
}

// Human labels for every axis the API can return, across both modes
// (set mode: 6 axes; burst mode: face-region axes). Unknown keys fall back
// to a title-cased label so the breakdown never breaks on a new axis.
const AXIS_LABELS = {
  sharpness: "Sharpness",
  exposure: "Exposure",
  expression: "Expression",
  camera_engagement: "Camera engagement",
  composition: "Composition",
  subject_focus: "Subject focus",
  face_sharpness: "Face sharpness",
  face_exposure: "Face exposure",
};

export function axisLabel(key) {
  if (AXIS_LABELS[key]) return AXIS_LABELS[key];
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}
