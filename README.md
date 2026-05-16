# PhotoRank

A self-hosted photo ranking tool that gets you from a pile of burst shots to a
confident pick — in under 30 seconds.

## The Problem

After a day of shooting on a phone, you end up with 100–200 photos. Culling
takes more time than most people have, so they sit unreviewed. PhotoRank gives
you a scored, ranked shortlist with a reason for each pick so you can trust the
result and move on.

## Two Modes

PhotoRank selects the right pipeline based on what you're doing.

### Burst mode (`--mode burst`)

For 2–6 near-identical photos from the same moment. No API call, no cost, no
network — just fast local scoring.

- **Face-region sharpness** via OpenCV Haar cascade: crops the face bounding
  box and runs Laplacian variance on that region. Full-image sharpness is
  nearly identical across a burst even when one frame has a sharp face and
  another doesn't — the crop is the right unit.
- **Face-region exposure**: brightness and contrast on the face crop.
- **Full-image sharpness and exposure** as secondary signals.

Validated: face-crop Laplacian variance shows 3× difference across a burst
set where full-image sharpness varied less than 6%.

### Set mode (`--mode set`, default)

For 7+ photos, or any set where semantic differences exist between shots.

**Layer 1 — deterministic (local, free)**
- Sharpness via combined Laplacian variance and Tenengrad gradient energy
- Exposure via histogram analysis (brightness, contrast, clipping)

**Layer 2 — semantic (Gemini 2.0 Flash)**
- Expression scored per-subject — weighted toward the weaker expression so a
  blink or flat look on one person can't be masked by a strong expression on
  the other
- Camera engagement — strict: any subject not looking at the camera scores ≤6
- Composition and framing
- Subject prominence and separation
- One specific, actionable note per photo

Both layers are merged by profile weights and ranked. Every result includes a
per-axis score breakdown so you can verify the ranking, not just accept it.

## Scoring Profiles

| Profile | Best for | Dominant axes |
|---|---|---|
| `family` | Group shots, portraits | expression, camera_engagement, subject_focus |
| `portrait` | Close-up portraits | sharpness, expression |
| `event` | Concerts, sport, street | composition, subject_focus |
| `travel` | Landscapes, travel portraits | composition (background is part of the shot) |
| `custom` | Your own priorities | all six axes, you choose the weights |

## Requirements

- Python 3.11+
- A Gemini API key (free tier covers typical use — set mode only)
- `opencv-python-headless`, `google-generativeai`, `python-dotenv`

```bash
pip install -r requirements.txt
```

## Setup

```bash
git clone https://github.com/jcube101/photorank
cd photorank
pip install -r requirements.txt
cp .env.example .env
# Add your Gemini API key to .env
```

## Running

Drop photos into `input/`, then:

```bash
# Burst mode — fast, no Gemini, for near-identical shots
python core/rank.py --mode burst

# Burst mode with explicit input path
python core/rank.py --mode burst --input /path/to/burst_set

# Set mode — full pipeline with Gemini (default)
python core/rank.py --profile family

# Point at a different folder
python core/rank.py --input /path/to/photos --profile portrait

# Tighten the blur gate (skip softer shots before Gemini)
python core/rank.py --profile portrait --blur-threshold 150

# Custom weights (all six axes must be provided and sum to 1.0)
python core/rank.py --profile custom \
  --weights '{"sharpness":0.2,"exposure":0.1,"expression":0.25,"composition":0.2,"subject_focus":0.2,"camera_engagement":0.05}'

# Save output to file
python core/rank.py --profile family --output output/results.json

# Deterministic scoring only (no Gemini, for diagnostics)
python core/score_tech.py input/ --threshold 100
```

The results JSON contains every photo's rank, final score, per-axis breakdown,
and — in set mode — Gemini's one-sentence note.

## Privacy

Photos are never stored. The only external service used is Gemini 2.0 Flash
(set mode only), which receives compressed images for semantic scoring and
nothing else. Burst mode is entirely local. No accounts, no cloud storage,
no telemetry.

## Status

**Phase 1 (CLI pipeline) is complete and validated on real photos.**
Phase 2 (FastAPI on Raspberry Pi) is in progress.

## What's Coming

- **Phase 2** — FastAPI on a Raspberry Pi, accessible from your phone via
  Cloudflare Tunnel
- **Phase 3** — Mobile-first PWA: tap to upload, swipe through results

See [ROADMAP.md](ROADMAP.md) for the full plan.

## Folder Structure

```
photorank/
├── core/
│   ├── ingest.py       collect, validate, compress images
│   ├── score_tech.py   deterministic scoring (sharpness, exposure)
│   ├── score_burst.py  face-region scoring for burst mode
│   ├── score_vision.py Gemini semantic scoring (set mode)
│   ├── rank.py         mode-aware pipeline — CLI entry point
│   └── profiles.py     all scoring profiles and weights
├── input/              drop photos here before running
├── output/             ranked results written here
├── api/                Phase 2: FastAPI wrapper (stub)
└── frontend/           Phase 3: React PWA (stub)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Phase 2 is open for contributions.

## License

MIT
