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

- **Phase 1 (CLI pipeline): In progress. Not yet tested on real photos.**
- All Phase 1 code is written: `blur_filter.py`, `scorer.py`, `ranker.py`
- Phase 1 gate: must be tested on real photos and scoring quality confirmed
  (top pick must agree with human's top pick >80% of test sets) before any
  Phase 2 code is written
- Do not touch `api/main.py` or `frontend/App.jsx` until Phase 1 is confirmed

---

## Architecture

| Layer | Technology | Why this choice |
|---|---|---|
| Frontend | React PWA | No App Store friction. Mobile-first. |
| Backend | FastAPI on Raspberry Pi | Cheap, private, already owned hardware |
| Tunnel | Cloudflare Tunnel | Exposes Pi to internet without opening firewall ports |
| Auth | Cloudflare Access | JWT-based, free for personal use, zero backend code needed |
| Technical scoring | OpenCV + MediaPipe | Local, free, deterministic, fast on Pi |
| Semantic scoring | Gemini 1.5 Flash | Near-free, reliable JSON output, strong at semantic tasks |
| Ranking | Python weighted scoring | Simple, auditable, swappable profiles |
| Storage | None (ephemeral) | Privacy requirement — photos deleted immediately post-scoring |
| Secrets | `.env` + python-dotenv | Standard, never committed |

---

## Scoring Architecture — Why Two Layers

**The core problem:** Gemini alone cannot reliably differentiate near-identical
burst shots on technical grounds. If you send it five shots of the same moment
taken half a second apart, it cannot tell which is sharpest or which has the
best eye contact. It sees them as approximately equal.

**The solution:** Split scoring by what each layer can actually do:

**Layer 1 — deterministic (blur_filter.py):** Objective, local, free.
- `sharpness`: Combined Laplacian variance + Tenengrad gradient energy.
  Both are needed — Laplacian alone is fooled by high-contrast OOF shots.
  Log-normalized to 1–10.
- `exposure`: Histogram analysis — mean brightness, contrast (std dev),
  highlight/shadow clipping fractions. Normalized to 1–10.
- `eye_openness`: MediaPipe Face Mesh EAR (eye aspect ratio). Worst of two
  eyes scored — catches single blinks. Returns `None` if no face; ranker
  redistributes that weight proportionally across the other five axes.
- `blur_raw`: Raw Laplacian variance (unscaled). Used as the blur gate to
  exclude images before Gemini is called. Default threshold: 100.

**Layer 2 — semantic (scorer.py, Gemini 1.5 Flash):** Only what Gemini can
reliably judge:
- `expression`: Emotional quality, mood, facial engagement
- `composition`: Framing, rule of thirds, visual balance, background
- `subject_focus`: Prominence and separation of the main subject
- `notes`: One specific, actionable sentence — the most important thing
  about this photo

**Gemini must never be asked about sharpness or exposure.** The prompt
explicitly tells it not to assess those — they are measured deterministically
and Gemini will produce meaningless noise on near-identical shots.

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
│   ├── score_tech.py       ← Layer 1: deterministic technical scorer
│   ├── score_vision.py     ← Layer 2: Gemini semantic scorer
│   ├── rank.py             ← merge layer, profile weights, CLI entry point
│   └── profiles.py         ← single source of truth for all profiles and weights
├── input/                  ← drop test photos here; contents git-ignored
├── output/                 ← ranked results written here; contents git-ignored
├── api/
│   └── main.py             ← Phase 2 stub — do not implement until Phase 1 gate
└── frontend/
    └── App.jsx             ← Phase 3 stub — do not implement until Phase 2 gate
```

---

## Phase 1 CLI — How to Run

```bash
# Full pipeline (reads from input/ by default)
python core/rank.py --profile family

# Explicit input path
python core/rank.py --input /path/to/photos --profile family

# Custom weights (all five axes required)
python core/rank.py --profile custom \
  --weights '{"sharpness":0.2,"exposure":0.1,"expression":0.25,"composition":0.2,"subject_focus":0.25}'

# Tighten blur gate
python core/rank.py --profile portrait --blur-threshold 150

# Save output to output/
python core/rank.py --profile family --output output/results.json

# Deterministic scorer standalone (no Gemini call)
python core/score_tech.py input/ --threshold 100
```

---

## Scoring Profiles

Five axes (eye_openness removed — MediaPipe unavailable on ARM64, original
weights redistributed proportionally). All weights must sum to 1.0. Raise
`ValueError` on load if violated.

| Profile | sharpness | exposure | expression | composition | subject_focus |
|---|---|---|---|---|---|
| family  | 0.19 | 0.10 | 0.31 | 0.15 | 0.25 |
| portrait| 0.27 | 0.16 | 0.27 | 0.10 | 0.20 |
| event   | 0.17 | 0.16 | 0.17 | 0.28 | 0.22 |
| travel  | 0.20 | 0.15 | 0.05 | 0.35 | 0.25 |
| custom  | user-supplied | | | | |

`travel` is for landscape and travel portrait shots where the background is
intentionally part of the composition. Composition is weighted highest (0.35).
Expression is minimal (0.05) — subject looking away or at scenery is acceptable.
The Gemini prompt includes a travel-specific hint instructing it not to penalise
prominent backgrounds and to reward subject-background harmony.

---

## Output Schema (per photo)

```json
{
  "photo_id":      "IMG_4821.jpg",
  "sharpness":     7.43,
  "exposure":      6.18,
  "expression":    8,
  "composition":   6,
  "subject_focus": 9,
  "relative_rank": 1,
  "notes":         "warm light catches the subject's left cheek, creating strong depth",
  "final_score":   8.012,
  "final_rank":    1,
  "score_breakdown": {
    "sharpness":    {"raw": 7.43, "weight": 0.19, "effective_weight": 0.19, "contribution": 1.412, "source": "deterministic"},
    "exposure":     {"raw": 6.18, "weight": 0.10, "effective_weight": 0.10, "contribution": 0.618, "source": "deterministic"},
    "expression":   {"raw": 8,   "weight": 0.31, "effective_weight": 0.31, "contribution": 2.48,  "source": "gemini"},
    "composition":  {"raw": 6,   "weight": 0.15, "effective_weight": 0.15, "contribution": 0.90,  "source": "gemini"},
    "subject_focus":{"raw": 9,   "weight": 0.25, "effective_weight": 0.25, "contribution": 2.25,  "source": "gemini"}
  }
}
```

See SPECS.md Section 5 for the complete contract and top-level output wrapper format.

---

## Gemini Integration

- **Model:** `gemini-2.0-flash` (override via `GEMINI_MODEL` in `.env`)
- **Auth:** `GEMINI_API_KEY` in `.env`
- **Batch size:** Up to 8 images per request
- **What to ask for:** `expression`, `composition`, `subject_focus`, `relative_rank`, `notes`
- **What NOT to ask:** sharpness, exposure, any technical quality assessment
- **On JSON parse failure:** strip markdown fences, retry once after 1s
- **On any failure after retries:** raise — do not assign default scores
- **Rate limit buffer:** 0.5s sleep between batches

The `notes` field must be one specific, actionable sentence. The Gemini prompt
enforces this with examples of good vs bad notes.

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

1. **No phase skipping.** Not a suggestion. Phase 1 must be confirmed on real
   photos before a single line of Phase 2 is written.
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
- `core/score_vision.py:score_photos()` — Gemini semantic scoring; accepts `photo_ids` list; abstraction point for swapping models
- `core/rank.py:rank_photos()` — merge + weight + rank
- `core/rank.py:_effective_weights()` — eye_openness null redistribution logic
- `core/profiles.py:PROFILES` — single source of truth for all profile weight dicts
- `core/profiles.py:validate_weights()` — called on load, raises if weights don't sum to 1.0

---

## Secrets

```
GEMINI_API_KEY=your_key_here
```

Never hardcode. Never commit `.env`. `.env.example` shows the required keys.
