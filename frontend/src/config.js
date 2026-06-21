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

// Profile weights — MUST mirror core/profiles.py PROFILES exactly. Used to
// re-rank an existing set-mode result under a different profile on-device,
// without re-uploading or re-calling Gemini (all six raw axis scores are
// already in the response). See src/lib/rerank.js.
export const AXES_DETERMINISTIC = ["sharpness", "exposure"];
export const AXES_SEMANTIC = [
  "expression",
  "composition",
  "subject_focus",
  "camera_engagement",
];
export const ALL_AXES = [...AXES_DETERMINISTIC, ...AXES_SEMANTIC];

export const PROFILE_WEIGHTS = {
  family: {
    expression: 0.25,
    camera_engagement: 0.2,
    subject_focus: 0.2,
    sharpness: 0.19,
    composition: 0.1,
    exposure: 0.06,
  },
  portrait: {
    sharpness: 0.27,
    expression: 0.27,
    subject_focus: 0.2,
    exposure: 0.16,
    composition: 0.1,
    camera_engagement: 0.0,
  },
  event: {
    composition: 0.28,
    subject_focus: 0.22,
    expression: 0.17,
    sharpness: 0.17,
    exposure: 0.16,
    camera_engagement: 0.0,
  },
  travel: {
    composition: 0.35,
    subject_focus: 0.25,
    sharpness: 0.2,
    exposure: 0.15,
    expression: 0.05,
    camera_engagement: 0.0,
  },
};

// `travel` is the only profile whose Gemini scoring differs (it injects a hint
// that rewards prominent backgrounds and de-emphasises expression). So
// re-ranking INTO travel from a non-travel run is directional, not identical —
// the weights are right but the raw composition/expression were judged without
// travel's leniency. We surface this caveat in the UI.
export const PROFILE_HINTED = new Set(["travel"]);

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
