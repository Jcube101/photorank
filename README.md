# PhotoRank

A self-hosted photo ranking tool that gets you from a pile of burst shots to a
confident pick — in under 30 seconds.

## The Problem

After a day of shooting on a phone, you end up with 100–200 photos. Culling
takes more time than most people have, so they sit unreviewed. PhotoRank gives
you a scored, ranked shortlist with a reason for each pick so you can trust the
result and move on.

## How It Works

Every photo goes through two scoring layers before it's ranked:

**Layer 1 — deterministic (local, no API cost)**
- Sharpness via combined Laplacian variance and Tenengrad gradient energy
- Exposure via histogram analysis (brightness, contrast, clipping)
- Eye openness via MediaPipe Face Mesh (catches blinks, no-face photos handled gracefully)

**Layer 2 — semantic (Gemini 1.5 Flash)**
- Expression and emotional quality
- Composition and framing
- Subject prominence

The two layers are merged by profile weights and ranked. Every result includes
a per-axis score breakdown and a one-sentence note from Gemini explaining the
most important thing about the photo — so you can verify the ranking, not just
accept it.

## Scoring Profiles

| Profile | Best for |
|---|---|
| `family` | Portraits, group shots — expression and eye openness weighted highest |
| `portrait` | Close-up portraits — sharpness and eye quality weighted highest |
| `event` | Concerts, sport, street — composition and subject prominence weighted highest |
| `custom` | Provide your own weights across all six axes |

## Requirements

- Python 3.11+
- A Gemini API key (free tier covers typical use)
- OpenCV, MediaPipe, google-generativeai (see `requirements.txt`)

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
# Rank photos in input/ with the family profile
python core/rank.py --profile family

# Point at a different folder
python core/rank.py --input /path/to/photos --profile portrait

# Tighten the blur gate (skip softer shots)
python core/rank.py --profile portrait --blur-threshold 150

# Custom weights (all six axes must be provided and sum to 1.0)
python core/rank.py --profile custom \
  --weights '{"sharpness":0.2,"exposure":0.1,"eye_openness":0.2,"expression":0.2,"composition":0.2,"subject_focus":0.1}'

# Save output to output/
python core/rank.py --profile family --output output/results.json

# Run deterministic scoring only (no Gemini call)
python core/score_tech.py input/ --threshold 100
```

The results JSON contains every photo's rank, final score, per-axis breakdown,
and Gemini's one-sentence note.

## Privacy

Photos are never stored. The only external service used is Gemini 1.5 Flash,
which receives images for semantic scoring and nothing else. No accounts, no
cloud storage, no telemetry.

## What's Coming

- **Phase 2** — FastAPI on a Raspberry Pi, accessible from your phone via
  Cloudflare Tunnel
- **Phase 3** — Mobile-first PWA: tap to upload, swipe through results

See [ROADMAP.md](ROADMAP.md) for the full plan.

## Status

Phase 1 (CLI pipeline) is in development and has not yet been validated on real
photos. Do not use for production workflows until the Phase 1 quality gate is
confirmed.

## Folder structure

```
photorank/
├── core/          ← scoring engine
│   ├── ingest.py       collect, validate, compress images
│   ├── score_tech.py   deterministic scoring (OpenCV + MediaPipe)
│   ├── score_vision.py Gemini semantic scoring
│   ├── rank.py         merge, weight, rank — CLI entry point
│   └── profiles.py     all scoring profiles and weights
├── input/         ← drop photos here before running
├── output/        ← ranked results written here
├── api/           ← Phase 2: FastAPI wrapper (not started)
└── frontend/      ← Phase 3: React PWA (not started)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Phase 1 is personal-use only —
contributions are welcome from Phase 3 onward.

## License

MIT
