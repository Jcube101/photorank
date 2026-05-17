# Contributing to PhotoRank

## Current Status

**Phase 1 (CLI pipeline) is complete.** **Phase 2 (FastAPI + Pi deployment) is
complete** — API is live at `https://photorank.job-joseph.com`. **Phase 3 (PWA
frontend) is the current focus** and open for contributions — open an issue to
discuss before starting work.

---

## Running Locally

See [README.md](README.md) for full setup instructions.

**CLI:**
```bash
python core/rank.py --profile family
```

**API server:**
```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8007 --reload
```

Test with:
```bash
curl http://localhost:8007/health
curl -X POST http://localhost:8007/rank \
  -F "images=@photo1.jpg" -F "images=@photo2.jpg" -F "profile=family"
```

---

## Branch Naming

```
feature/<short-description>    New functionality
fix/<short-description>        Bug fix
docs/<short-description>       Documentation only
```

Examples: `feature/moment-grouping`, `fix/face-detection-crash`,
`docs/api-contract`

---

## What a Good PR Looks Like

- Targets one thing. If you're fixing a bug and noticed a refactor opportunity,
  open two PRs.
- Includes a description of *why*, not just *what*.
- Has been tested against real photos, not just synthetic data. Mention the
  test conditions (number of photos, subject type, mode and profile used).
- Does not break the score breakdown. The `score_breakdown` field in the output
  is part of the contract — every axis must appear with its expected fields.
- Does not add a new dependency without a clear reason. The Pi has limited RAM.

---

## Adding a New Scoring Profile

Profiles live in `core/profiles.py` in the `PROFILES` dict.

Rules:
1. All six axes must be present: `sharpness`, `exposure`, `expression`,
   `composition`, `subject_focus`, `camera_engagement`.
2. Weights must sum to exactly 1.0 (the validator enforces this at runtime —
   check it yourself first).
3. `camera_engagement` carries weight only in `family` (0.20). For profiles
   where camera engagement doesn't matter (landscape, sport), set it to 0.00
   — it will still be scored by Gemini but won't affect the final score.
4. Include a short comment in the PR explaining the intended use case and why
   the weights are set as they are.
5. Test the profile against at least one real photo set before submitting.

```python
"street": {
    "composition":       0.30,
    "subject_focus":     0.25,
    "sharpness":         0.20,
    "expression":        0.15,
    "exposure":          0.10,
    "camera_engagement": 0.00,
},
```

---

## Adding a New Burst Signal

Burst mode signals live in `core/score_burst.py`. Each signal must:

1. Be purely deterministic — no network, no API call.
2. Return a float in [1.0, 10.0].
3. Fall back gracefully when the required image feature isn't present (e.g. no
   face detected). Return the full-image equivalent, not null.
4. Be added to `BURST_WEIGHTS` in `core/profiles.py` with weights that still
   sum to 1.0 (adjust other burst weights accordingly).
5. Be documented in SPECS.md Section 3b with the normalization formula.

---

## Swapping the Vision Model

The semantic scoring abstraction point is `score_photos()` in
`core/score_vision.py`. Any replacement vision model must satisfy this contract:

**Input:** `list[str | Path]` of image paths (sharp images only), plus
`photo_ids` list and `profile` string.

**Output:** `list[dict]` where each dict contains:

```json
{
  "photo_id":             "IMG_4821.jpg",
  "subject_1_expression": 8,
  "subject_2_expression": 5,
  "expression":           6.95,
  "camera_engagement":    9,
  "composition":          6,
  "subject_focus":        9,
  "relative_rank":        1,
  "notes":                "one specific actionable sentence"
}
```

Rules that must hold regardless of which model is used:
- `subject_1_expression` must be int/float in [1, 10]
- `subject_2_expression` must be int/float in [1, 10] or null
- `expression` is computed from the per-subject values by `_parse_scores`:
  single subject → subject_1; two subjects → lower × 0.65 + upper × 0.35
- `camera_engagement`, `composition`, `subject_focus` must be int/float in [1, 10]
- `relative_rank` must be a unique integer in [1, batch_size]
- `notes` must be one sentence, specific and actionable
- All fields must be present for every photo in the input
- On failure, raise — do not return partial results or default scores
- Never ask the model to score sharpness or exposure

If you add a new backend (e.g. Claude, GPT-4o), implement it as a separate
function alongside `score_photos()` and wire it in via a `--model` flag in
`rank.py`. Do not modify the existing Gemini implementation.

---

## Data Privacy Rules

These rules cannot be violated in any PR. They are non-negotiable:

1. **No photo persistence.** Images may only exist in memory or on disk during
   active scoring. Delete immediately after use — before any response is sent
   or any return value is passed back to the caller.

2. **No logging of image content.** Do not log filenames, file sizes, EXIF
   data, pixel values, or any other information that could identify an image or
   its subject. Error logs may include the exception type and message only.

3. **No caching.** Do not cache images, scores, or any derivative between
   requests in any store (memory, filesystem, database).

4. **EXIF must be stripped before external calls.** Phone photos contain GPS
   coordinates. Strip EXIF before sending any image to Gemini or any other
   external service. Burst mode is entirely local — no stripping needed.

5. **Training consent is explicit only.** If any future version collects data
   for model training, it must be behind an explicit opt-in. Never infer
   consent from usage. Never train on data from users who have not opted in.

PRs that violate any of these rules will not be merged.
