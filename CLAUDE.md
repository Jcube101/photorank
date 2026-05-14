# PhotoRank — Claude Source of Truth

## What This Is

PhotoRank is a mobile-first photo ranking tool. After a day of shooting on a
phone, you end up with 100–200 photos across multiple moments. Culling takes
more time than most people have, so photos sit unreviewed. PhotoRank gets you
from 200 photos to a confident, post-ready shortlist without spending an evening
on it.

**Two archetypes:**
- **v1 (burst):** 5–20 similar burst shots of one moment. Wants the best one
  picked and explained in under 30 seconds.
- **v2 (full day):** 200 photos from a full day. Wants a post-ready shortlist
  grouped by moment, best shot per group surfaced automatically.

**Core design principle: Verify, don't trust.** Always show score breakdown
and reasoning — transparency is the core feature, not just accuracy.

---

## Architecture

| Layer | Technology | Notes |
|---|---|---|
| Frontend | React PWA | Mobile-first, no App Store friction |
| Backend | FastAPI | Runs on Raspberry Pi |
| Tunnel | Cloudflare Tunnel | Exposes Pi endpoint publicly |
| Auth | Cloudflare Access | Gate on the tunnel endpoint |
| Technical scoring | OpenCV (Laplacian + Tenengrad + histogram) + MediaPipe Face Mesh | Deterministic, local, free, fast |
| Semantic scoring | Gemini 1.5 Flash | Expression, composition, subject prominence only |
| Ranking | Python two-layer weighted scoring | Swappable profiles |
| Storage | None (ephemeral) | Photos deleted from Pi immediately post-scoring |
| Secrets | `.env` + python-dotenv | Never hardcoded, never committed |

---

## Build Sequence

Do **not** advance to the next phase until the current phase is confirmed working.

| Phase | Scope | Status |
|---|---|---|
| 1 | CLI pipeline: blur filter + Gemini scoring + ranking | In progress |
| 2 | FastAPI on Pi wrapping Phase 1 logic | Not started |
| 3 | Mobile-first PWA UI | Not started |
| 4 | Client-side image compression before upload | Not started |
| 5 | Cloudflare Access auth gate | Not started |

**Phase 1 gate:** Must be tested against real photos with scoring quality
confirmed before Phase 2 begins.

---

## Folder Structure

```
photorank/
├── CLAUDE.md               ← this file
├── .gitignore
├── .env                    ← secrets (never committed)
├── requirements.txt        ← Phase 1 deps
├── phase1/
│   ├── blur_filter.py      ← deterministic technical scorer (sharpness/exposure/eye_openness)
│   ├── scorer.py           ← Gemini semantic scorer (expression/composition/subject_focus)
│   └── ranker.py           ← two-layer merge, profile weights, CLI entry point
├── api/
│   └── main.py             ← Phase 2: FastAPI wrapper
└── frontend/
    └── App.jsx             ← Phase 3: React PWA
```

---

## Phase 1 CLI Pipeline

### Running the pipeline

```bash
# Score a folder of photos, output ranked JSON
python phase1/ranker.py --input /path/to/photos --profile family

# Score with custom weights (all six axes required)
python phase1/ranker.py --input /path/to/photos --profile custom \
  --weights '{"sharpness":0.2,"exposure":0.1,"eye_openness":0.2,"expression":0.2,"composition":0.2,"subject_focus":0.1}'

# Adjust blur gate threshold
python phase1/ranker.py --input /path/to/photos --profile portrait --blur-threshold 80

# Run just the deterministic scorer standalone
python phase1/blur_filter.py /path/to/photos --threshold 100
```

### Module responsibilities

- **blur_filter.py** — Layer 1 deterministic scorer. Computes three objective
  technical metrics per image:
  - **sharpness** — combined Laplacian variance + Tenengrad gradient energy,
    normalized to 1–10. Two measures together resist false positives (e.g. a
    high-contrast out-of-focus shot can fool Laplacian alone).
  - **exposure** — histogram analysis: mean brightness, contrast (std dev),
    highlight/shadow clipping fractions → 1–10.
  - **eye_openness** — MediaPipe Face Mesh EAR (eye aspect ratio). Scores the
    worse of the two eyes so a single blink fails the shot. Returns `None` when
    no face is detected; ranker redistributes that axis's weight automatically.
  Raw Laplacian variance (`blur_raw`) is also returned and used as the blur gate
  to exclude images before Gemini is called.

- **scorer.py** — Layer 2 semantic scorer. Sends sharp images to Gemini 1.5
  Flash in batches of up to 8. Asks only for semantic attributes Gemini can
  reliably judge: `expression`, `composition`, `subject_focus`, and `notes`.
  Does **not** ask Gemini to assess sharpness or exposure — those are already
  measured deterministically and Gemini cannot differentiate near-identical
  burst shots on technical grounds. Handles retries and JSON parse errors.

- **ranker.py** — Merge and ranking layer. Runs deterministic scoring first
  (doubles as blur gate), then Gemini, merges by `photo_id`, applies profile
  weights, and outputs ranked JSON. CLI entry point for Phase 1.

---

## Scoring Architecture

### Layer 1 — Deterministic (blur_filter.py)

Computed locally for every image before any API call:

| Axis | Method | What it measures |
|---|---|---|
| `sharpness` | Laplacian variance + Tenengrad, log-normalized to 1–10 | Combined focus and edge energy |
| `exposure` | Histogram mean + std + clipping fractions → 1–10 | Brightness balance and dynamic range |
| `eye_openness` | MediaPipe Face Mesh EAR, worst eye → 1–10 or `null` | Blink detection and eye engagement |

`blur_raw` (raw Laplacian variance) is also computed and used as the blur gate.
Images below `--blur-threshold` (default 100) are excluded before Gemini is called.

### Layer 2 — Semantic (scorer.py, Gemini 1.5 Flash)

Gemini scores only attributes requiring visual understanding — three axes:

```json
{
  "photo_id": "img_001",
  "expression": 9,
  "composition": 7,
  "subject_focus": 8,
  "notes": "one specific sentence — most important thing about this photo"
}
```

`notes` must be one specific, actionable sentence — not a generic description.

### Merged output per photo (ranker.py)

```json
{
  "photo_id": "img_001",
  "sharpness": 8.4,
  "exposure": 7.1,
  "eye_openness": 9.2,
  "expression": 9,
  "composition": 7,
  "subject_focus": 8,
  "notes": "...",
  "final_score": 8.31,
  "final_rank": 1,
  "score_breakdown": {
    "sharpness":    {"raw": 8.4, "weight": 0.15, "effective_weight": 0.15, "contribution": 1.26, "source": "deterministic"},
    "eye_openness": {"raw": 9.2, "weight": 0.20, "effective_weight": 0.20, "contribution": 1.84, "source": "deterministic"},
    "expression":   {"raw": 9,   "weight": 0.25, "effective_weight": 0.25, "contribution": 2.25, "source": "gemini"},
    "..."
  }
}
```

When `eye_openness` is `null` (no face detected), its weight is redistributed
proportionally across the other five axes. The `score_breakdown` records
`effective_weight` so the user sees exactly what contributed to the final score.

---

## Scoring Profiles

Six axes: `sharpness`, `exposure`, `eye_openness` (deterministic) + `expression`,
`composition`, `subject_focus` (Gemini). All weights must sum to 1.0.

| Profile | eye_openness | expression | subject_focus | sharpness | composition | exposure |
|---|---|---|---|---|---|---|
| family | 0.20 | 0.25 | 0.20 | 0.15 | 0.12 | 0.08 |
| portrait | 0.25 | 0.20 | 0.15 | 0.20 | 0.08 | 0.12 |
| event | 0.10 | 0.15 | 0.20 | 0.15 | 0.25 | 0.15 |
| custom | user-defined via UI sliders | | | | | |

---

## Gemini Integration

- **Model:** `gemini-1.5-flash`
- **Auth:** `GEMINI_API_KEY` in `.env`
- **What Gemini scores:** `expression`, `composition`, `subject_focus`, `notes` only.
  Sharpness and exposure are excluded from the Gemini prompt — Gemini cannot
  reliably differentiate burst shots on technical quality.
- **Prompt strategy:** Send up to 8 images per batch request. System prompt
  explicitly tells Gemini not to assess technical sharpness or exposure.
  Ask for a JSON array.
- **Error handling:** On JSON parse failure, retry once. On API error, surface
  clearly — do not silently assign zero scores.

---

## Data & Privacy

- **v1:** Photos deleted post-scoring, no logs, nothing persisted.
- **v2 public:** Free tier — opt-in consent for training data. Paid tier — no
  training guarantee. Consent is **explicit only, never assumed**.
- User table must include `training_consent boolean` from first public release.

---

## Secrets

Required in `.env`:

```
GEMINI_API_KEY=your_key_here
```

Never hardcode. Never commit `.env`. The `.gitignore` already excludes it.

---

## Development Rules

1. **No phase skipping.** Phase 1 must be confirmed working on real photos
   before writing a single line of Phase 2.
2. **Transparency always.** Every score shown to the user must include the
   breakdown (per-axis scores + weights) and the Gemini notes. Never show only
   a final number.
3. **Photos never persist.** Delete uploaded photos immediately after scoring.
   This is a hard requirement, not a nice-to-have.
4. **Weights sum to 1.0.** Validate this on load; raise clearly if violated.
5. **No silent failures.** If Gemini returns bad JSON or errors, surface it
   loudly. Don't assign default scores.
6. **Test with real photos.** Synthetic test data is not a substitute for
   confirming scoring quality on actual phone photos.
