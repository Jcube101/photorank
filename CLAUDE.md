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
| Blur filter | OpenCV Laplacian variance | Local, free, fast |
| Vision scoring | Gemini 1.5 Flash | Near-free, strong JSON output |
| Ranking | Python weighted scoring | Swappable profiles |
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
│   ├── blur_filter.py      ← OpenCV Laplacian blur detection
│   ├── scorer.py           ← Gemini 1.5 Flash vision scoring
│   └── ranker.py           ← weighted scoring + CLI entry point
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

# Score with custom weights
python phase1/ranker.py --input /path/to/photos --profile custom \
  --weights '{"sharpness": 0.4, "expression": 0.3, "composition": 0.1, "exposure": 0.1, "subject_focus": 0.1}'

# Pipe through blur filter first (skips obviously blurry photos)
python phase1/ranker.py --input /path/to/photos --profile portrait --blur-threshold 100
```

### Module responsibilities

- **blur_filter.py** — Computes Laplacian variance for each image. Returns a
  blur score; images below the threshold are flagged/excluded before sending to
  Gemini. Keeps costs down and prevents wasting API quota on unusable shots.

- **scorer.py** — Sends images to Gemini 1.5 Flash in batches. Returns
  structured JSON per photo matching the scoring schema. Handles retries and
  JSON parse errors gracefully.

- **ranker.py** — Applies profile weights to Gemini scores, produces a final
  ranked list, and prints results to stdout as JSON. CLI entry point for
  Phase 1.

---

## Scoring Schema

Every photo gets this JSON from Gemini (scores 1–10):

```json
{
  "photo_id": "img_001",
  "sharpness": 8,
  "expression": 9,
  "composition": 7,
  "exposure": 8,
  "subject_focus": 9,
  "notes": "one specific sentence — most important thing about this photo",
  "rank": 1
}
```

`notes` must be one specific, actionable sentence — not a generic description.

---

## Scoring Profiles

Weights across five axes: `sharpness`, `expression`, `composition`, `exposure`,
`subject_focus`. All weights in a profile must sum to 1.0.

| Profile | expression | subject_focus | sharpness | composition | exposure |
|---|---|---|---|---|---|
| family | 0.35 | 0.25 | 0.20 | 0.12 | 0.08 |
| portrait | 0.25 | 0.25 | 0.30 | 0.05 | 0.15 |
| event | 0.10 | 0.25 | 0.20 | 0.30 | 0.15 |
| custom | user-defined via UI sliders | | | | |

---

## Gemini Integration

- **Model:** `gemini-1.5-flash`
- **Auth:** `GEMINI_API_KEY` in `.env`
- **Prompt strategy:** Send up to 8 images per batch request. Include the
  scoring schema and profile context in the system prompt. Ask for a JSON array.
- **Error handling:** On JSON parse failure, retry once with a stricter prompt.
  On API error, surface clearly — do not silently assign zero scores.

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
