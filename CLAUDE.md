# PhotoRank — AI Agent Context

This file is for AI coding agents (Claude Code, Claude, etc.) picking up this
project. It captures everything needed to resume work without re-explanation.
Update it whenever a decision changes or a discovery is made.

For humans: see README.md. For precise technical contracts: see SPECS.md.

---

## Problem and Users

People take 100–200 phone photos in a day and never cull them because it takes
too long. PhotoRank does the culling automatically and explains each pick.

**Two archetypes — design for both:**

- **Archetype 1 (burst, v1):** Has 5–20 near-identical burst shots of one
  moment. Wants the best one picked and explained in under 30 seconds. This
  is the current target. The core challenge: the shots are nearly identical,
  so Gemini alone cannot differentiate them reliably (see Learnings).

- **Archetype 2 (full day, v2):** Has 200 photos from a full day across
  multiple moments. Wants a post-ready shortlist grouped by moment, best shot
  per group surfaced automatically. Not in scope yet — do not design for it.

**Core design principle:** Verify, don't trust. Always show score breakdown
and reasoning. Transparency is the core feature, not just accuracy. A user
must be able to look at the breakdown and understand why a photo ranked where
it did. Never show only a final number.

---

## Current State

- **Phase 1 (CLI pipeline): Complete. Validated on real photos.**
- **Phase 2 (FastAPI + Pi deployment): Complete. Validated on real device.**
- **Phase 3 (React PWA): Built. On-device golden-path validation is the remaining gate.**
- Two-mode pipeline: `--mode burst` (deterministic only) and `--mode set` (default, Gemini)
- Phase 1 gate passed: both modes validated, top pick agreement >80% on real test sets
- Phase 2 gate passed: end-to-end validated on iPhone via `photorank.job-joseph.com`
- `api/main.py`: `POST /rank`, `GET /health`, port **8007**, live at `photorank.job-joseph.com`
- `frontend/` is a React + Vite PWA: upload → loading → results, with client-side
  compression, dynamic per-axis breakdowns, manifest + service worker (offline shell)
- Remaining Phase 3 work: on-device test (iPhone Safari), Lighthouse PWA audit,
  non-technical user completes the flow unaided

---

## Architecture

| Layer | Technology | Why this choice |
|---|---|---|
| Frontend | React PWA | No App Store friction. Mobile-first. |
| Backend | FastAPI on Raspberry Pi, port 8007 | Cheap, private, already owned hardware |
| Tunnel | Cloudflare Tunnel → `photorank.job-joseph.com` | Exposes Pi to internet without opening firewall ports |
| Auth | Cloudflare Access | JWT-based, free for personal use, zero backend code needed |
| Technical scoring | OpenCV (Haar cascade) | Local, free, deterministic, fast on Pi — MediaPipe removed (no ARM64 wheels) |
| Semantic scoring | Gemini 2.0 Flash | Near-free, reliable JSON output, strong at semantic tasks |
| Ranking | Python weighted scoring | Simple, auditable, swappable profiles |
| Storage | None (ephemeral) | Privacy requirement — photos deleted immediately post-scoring |
| Secrets | `.env` + python-dotenv | Standard, never committed |

---

## Scoring Architecture — Two Modes

**The core problem:** Gemini cannot reliably differentiate near-identical burst
shots. Scores cluster identically regardless of prompt engineering. Vision LLMs
are not suitable as the primary differentiator for true bursts.

**The solution:** Two modes, each doing what it can actually do.

### Burst Mode (`--mode burst`)

For 2–6 near-identical photos from the same moment. Gemini is skipped entirely.

**Deterministic signals only:**
- `sharpness`: Full-image Laplacian + Tenengrad, log-normalized 1–10.
- `exposure`: Full-image histogram analysis 1–10.
- `face_sharpness`: Laplacian variance on the face bounding box crop (OpenCV
  Haar cascade). Catches per-face focus differences that full-image sharpness
  misses — one burst frame can have a sharp face and another a slightly soft one
  while full-image sharpness is identical.
- `face_exposure`: Exposure score on the face crop. Face lighting can differ
  within a burst when the subject turns slightly.
- `blur_raw` / `face_blur_raw`: Raw Laplacian values for diagnostics.

**BURST_WEIGHTS**: face_sharpness 0.50, sharpness 0.20, face_exposure 0.20, exposure 0.10

### Set Mode (`--mode set`, default)

For 7+ photos, or any varied photo set where semantic differences exist.

**Layer 1 — deterministic (score_tech.py):** sharpness, exposure, blur_raw.
Blur gate excludes images below threshold before Gemini is called.

**Layer 2 — semantic (score_vision.py, Gemini 2.0 Flash):** Only what Gemini
can reliably judge across varied photos:
- `expression`: Per-subject emotional quality, weighted toward weaker subject.
- `camera_engagement`: Direct eye contact strictness (≤6 if anyone looks away).
- `composition`: Framing, rule of thirds, visual balance, background.
- `subject_focus`: Prominence and separation of the main subject.
- `notes`: One specific, actionable sentence — the most important thing.
- `relative_rank`: Gemini's holistic ordering, used as a tiebreaker.

**Gemini must never be asked about sharpness or exposure** — it produces
meaningless noise on near-identical shots for technical axes.

---

## Folder Structure

```
photorank/
├── CLAUDE.md               ← this file
├── README.md               ← public-facing project overview
├── SPECS.md                ← precise technical contracts (implement from here)
├── ROADMAP.md              ← phased plan with explicit gates
├── LEARNINGS.md            ← living log of discoveries that changed the approach
├── CONTRIBUTING.md         ← contributor guide (Phase 3+)
├── .gitignore
├── .env                    ← secrets (never committed)
├── .env.example            ← template showing required keys
├── requirements.txt        ← Phase 1 deps
├── core/                   ← scoring engine (everything wraps this)
│   ├── ingest.py           ← collect images, validate formats, compress to ~1.5 MP
│   ├── score_tech.py       ← deterministic scorer (sharpness, exposure)
│   ├── score_burst.py      ← burst mode: face-region sharpness + exposure
│   ├── score_vision.py     ← Gemini semantic scorer (set mode only)
│   ├── rank.py             ← mode-aware pipeline, profile weights, CLI entry point
│   └── profiles.py         ← single source of truth for all profiles and weights
├── input/                  ← drop test photos here; contents git-ignored
├── output/                 ← ranked results written here; contents git-ignored
├── api/
│   └── main.py             ← Phase 2 — FastAPI wrapper (complete)
└── frontend/               ← Phase 3 — React + Vite mobile PWA (built)
    ├── index.html          ← entry: viewport, fonts, manifest, theme-color
    ├── vite.config.js
    ├── package.json
    ├── public/
    │   ├── manifest.webmanifest  ← name, icons, theme_color #f6f4ef
    │   ├── sw.js                 ← offline app shell; never caches /rank
    │   └── icons/                ← icon-192.png, icon-512.png (any+maskable)
    ├── scripts/
    │   └── gen-icons.mjs         ← regenerates the PWA icons (no deps)
    ├── src/
    │   ├── main.jsx              ← mount + service-worker registration
    │   ├── App.jsx               ← state machine: upload → loading → results
    │   ├── styles.css            ← all design tokens + component CSS
    │   ├── config.js             ← API_BASE, PROFILES, axis labels, limits
    │   ├── api.js                ← rankPhotos() — multipart POST + error mapping
    │   ├── compress.js           ← client-side downscale ~1.5 MP, HEIC→JPEG
    │   ├── usePwaInstall.js       ← Add-to-Home-Screen prompt (after 1st success)
    │   ├── lib/breakdown.js      ← normalizes API score_breakdown → bar rows
    │   ├── screens/              ← UploadScreen, LoadingScreen, ResultsScreen
    │   └── components/           ← HeroCard, RankCard, AxisBar, MessageScreen
    └── _design/                  ← archived Claude Design reference (NOT production)
```

---

## Phase 1 CLI — How to Run

```bash
# Burst mode — deterministic only, no Gemini (2–6 near-identical shots)
python core/rank.py --mode burst

# Burst mode with explicit input path
python core/rank.py --mode burst --input /path/to/burst_set

# Set mode — full pipeline with Gemini (default)
python core/rank.py --profile family

# Explicit input path
python core/rank.py --input /path/to/photos --profile family

# Custom weights (all six axes required)
python core/rank.py --profile custom \
  --weights '{"sharpness":0.2,"exposure":0.1,"expression":0.25,"composition":0.2,"subject_focus":0.2,"camera_engagement":0.05}'

# Tighten blur gate
python core/rank.py --profile portrait --blur-threshold 150

# Save output to output/
python core/rank.py --profile family --output output/results.json

# Deterministic scorer standalone (no Gemini call)
python core/score_tech.py input/ --threshold 100
```

---

## Scoring Profiles

Six axes. All weights must sum to 1.0. Raise `ValueError` on load if violated.
(`eye_openness` removed — MediaPipe unavailable on ARM64; `camera_engagement`
added as a Gemini-scored axis, weighted 0.00 in profiles that don't use it.)

| Profile | sharpness | exposure | expression | camera_engagement | composition | subject_focus |
|---|---|---|---|---|---|---|
| family  | 0.19 | 0.06 | 0.25 | 0.20 | 0.10 | 0.20 |
| portrait| 0.27 | 0.16 | 0.27 | 0.00 | 0.10 | 0.20 |
| event   | 0.17 | 0.16 | 0.17 | 0.00 | 0.28 | 0.22 |
| travel  | 0.20 | 0.15 | 0.05 | 0.00 | 0.35 | 0.25 |
| custom  | user-supplied — all six axes required | | | | | |

`camera_engagement` is scored by Gemini on every run. It carries weight only
in `family` (0.20); other profiles include it at 0.00 so it appears in output
but does not affect the score. Strict rule: any subject not looking at the
camera caps the score at 6; all subjects at camera starts at 8.

`travel` is for landscape and travel portrait shots where the background is
intentionally part of the composition. Composition is weighted highest (0.35).
Expression is minimal (0.05) — subject looking away or at scenery is acceptable.
The Gemini prompt includes a travel-specific hint instructing it not to penalise
prominent backgrounds and to reward subject-background harmony.

---

## Output Schema (per photo, set mode, family profile)

```json
{
  "photo_id":             "IMG_4821.jpg",
  "sharpness":            7.43,
  "exposure":             6.18,
  "subject_1_expression": 8,
  "subject_2_expression": 6,
  "expression":           6.7,
  "camera_engagement":    9,
  "composition":          6,
  "subject_focus":        9,
  "relative_rank":        1,
  "notes":                "warm light on subject 1 but subject 2's eyes are slightly closed",
  "final_score":          7.841,
  "final_rank":           1,
  "score_breakdown": {
    "sharpness":          {"raw": 7.43, "weight": 0.19, "effective_weight": 0.19, "contribution": 1.412, "source": "deterministic"},
    "exposure":           {"raw": 6.18, "weight": 0.06, "effective_weight": 0.06, "contribution": 0.371, "source": "deterministic"},
    "expression":         {"raw": 6.7,  "weight": 0.25, "effective_weight": 0.25, "contribution": 1.675, "source": "gemini"},
    "camera_engagement":  {"raw": 9,    "weight": 0.20, "effective_weight": 0.20, "contribution": 1.800, "source": "gemini"},
    "composition":        {"raw": 6,    "weight": 0.10, "effective_weight": 0.10, "contribution": 0.600, "source": "gemini"},
    "subject_focus":      {"raw": 9,    "weight": 0.20, "effective_weight": 0.20, "contribution": 1.800, "source": "gemini"}
  }
}
```

`expression` is computed from per-subject scores: `lower * 0.65 + upper * 0.35`.
See SPECS.md Section 5 for the complete contract and top-level output wrapper format.

---

## Gemini Integration

- **Model:** `gemini-2.0-flash` (override via `GEMINI_MODEL` in `.env`)
- **Auth:** `GEMINI_API_KEY` in `.env`
- **Batch size:** Up to 8 images per request
- **What to ask for:** `subject_1_expression`, `subject_2_expression`, `camera_engagement`, `composition`, `subject_focus`, `relative_rank`, `notes` (Python computes `expression` from per-subject values)
- **What NOT to ask:** sharpness, exposure, any technical quality assessment
- **On JSON parse failure:** strip markdown fences, retry once after 1s
- **On any failure after retries:** raise — do not assign default scores
- **Rate limit buffer:** 0.5s sleep between batches

The `notes` field must be one specific, actionable sentence. The Gemini prompt
enforces this with examples of good vs bad notes.

---

## Phase 3 Frontend — React PWA

Mobile-first PWA (390px base) in `frontend/`. Built from the approved Claude
Design prototype, now archived in `frontend/_design/` (`PhotoRank.HTML`,
`App.jsx`, `ios-frame.jsx`, `image-slot.js`) — reference only, never imported.

**Stack:** React 18 + Vite, plain CSS (no UI framework). Fonts: Instrument
Serif (editorial headlines), Geist (body), Geist Mono (labels). All design
tokens live in `src/styles.css` `:root` (warm off-white `--bg #f6f4ef`,
near-black `--ink`, olive `--accent`).

**Three screens** (state machine in `src/App.jsx`):
1. **Upload** — tagline, dashed drop zone (tap or drag), 2–20 file validation
   (inline error), profile selector (family/portrait/travel/event).
2. **Loading** — "Reading N photos for the moment that lands", animated stage
   progress (Decoding → Detecting subjects → Scoring axes → Ranking; the
   Gemini "Scoring axes" step is hidden when ≤6 files, a cosmetic burst guess),
   per-photo thumbnails that check off as they compress. Subtitle is
   "Scoring via PhotoRank AI" — **not** "on-device".
3. **Results** — hero №1 card (real photo, final score, profile, AI note in
   quotes) + runners-up cards that expand to the full score breakdown.

**Key contracts:**
- **Breakdown is rendered dynamically from the API's `score_breakdown`** — never
  hardcoded. Works for both modes (6 set-mode axes incl. zero-weight
  `camera_engagement`; 4 burst-mode face/full axes). See `src/lib/breakdown.js`.
- **Bar width = contribution** (weight × raw), with the explicit math on each
  row and a dashed cap at the axis max. Gemini axes use the accent colour.
- **Mode is omitted from the request** so the server auto-detects (EXIF).
- **Client-side compression** (`src/compress.js`) downscales to ~1.5 MP and
  re-encodes JPEG; this both shrinks the upload and yields a browser-renderable
  preview (HEIC otherwise won't display). Filename is preserved so the server's
  `photo_id` maps back to each preview. On decode failure: send the original,
  fall back to a gradient placeholder.
- **Errors are mapped to friendly copy** in `src/api.js` (422/500/502 →
  user-facing messages); raw server detail is never shown.
- **Privacy on the client:** previews are object URLs, revoked on reset. No
  history, no accounts, no persistence.
- **PWA:** `public/manifest.webmanifest` + `public/sw.js` (offline app shell;
  **never caches `/rank`**). Add-to-Home-Screen prompt fires after the first
  successful ranking (`src/usePwaInstall.js`).

**Run:** `cd frontend && npm install && npm run dev` (or `npm run build`).
Override the API with `VITE_API_BASE` (default `https://photorank.job-joseph.com`).

---

## Data Privacy — Non-Negotiable Rules

These cannot be relaxed for any reason:

1. **Photos never persist.** Delete from disk immediately after scoring,
   before the response is returned. Use try/finally to guarantee this.
2. **No logging of image content.** No filenames, sizes, EXIF, or pixel data
   in any log.
3. **No caching.** Nothing persists between requests.
4. **Strip EXIF before external calls.** Phone photos contain GPS coordinates.
   (Phase 2 responsibility — not yet implemented.)
5. **Training consent is explicit.** If v2 ever collects data for training, it
   must be behind an explicit opt-in. Never assumed from usage.

---

## Development Rules

1. **No phase skipping.** Phase 1 is complete. Phase 2 is the current focus.
   Phase 3 (PWA) does not start until Phase 2 is confirmed on real device.
2. **Transparency always.** Every score must include the full breakdown. Never
   surface only a final number.
3. **No silent failures.** If Gemini fails, surface it loudly. No default scores.
4. **Weights must sum to 1.0.** Validate on load. Raise clearly if violated.
5. **Test with real photos.** Synthetic data does not validate scoring quality.
6. **Update LEARNINGS.md** when real-photo testing reveals something unexpected.
7. **Update this file** when architectural decisions change.

---

## Key Files to Know

- `core/ingest.py:ingest()` — collect + validate + compress, returns (photos, temp_dir)
- `core/ingest.py:cleanup()` — always call this after scoring; deletes temp files
- `core/score_tech.py:compute_technical_scores()` — single-image deterministic scoring; accepts `photo_id` override
- `core/score_tech.py:compute_technical_scores_batch()` — batch version
- `core/score_burst.py:compute_burst_scores()` — face-region burst scoring; accepts `photo_id` override
- `core/score_burst.py:compute_burst_scores_batch()` — batch version
- `core/score_vision.py:score_photos()` — Gemini semantic scoring; accepts `photo_ids` list; set mode only
- `core/rank.py:rank_photos()` — merge + weight + rank (set mode)
- `core/rank.py:_rank_burst()` — apply BURST_WEIGHTS, sort by final_score (burst mode)
- `core/profiles.py:PROFILES` — single source of truth for all profile weight dicts
- `core/profiles.py:BURST_WEIGHTS` — burst mode axis weights (face_sharpness, sharpness, face_exposure, exposure)
- `core/profiles.py:validate_weights()` — called on load, raises if weights don't sum to 1.0
- `api/main.py:rank_endpoint()` — FastAPI POST /rank handler; saves uploads, auto-detects mode, calls ingest → score → rank, cleans up
- `api/main.py:_auto_detect_mode()` — reads EXIF DateTimeOriginal from raw uploads; returns 'burst' if ≤6 files within 10s, else 'set'
- `api/main.py:_run_burst()` — burst scoring sub-routine (called after EXIF stripping by ingest)
- `api/main.py:_run_set()` — set mode scoring sub-routine (deterministic → Gemini → merge → rank)

---

## Secrets

```
GEMINI_API_KEY=your_key_here
```

Never hardcode. Never commit `.env`. `.env.example` shows the required keys.
