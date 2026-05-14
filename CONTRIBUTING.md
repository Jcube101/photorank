# Contributing to PhotoRank

## Current Status

**Phase 1 (CLI pipeline) is personal-use only.** Contributions are not being
accepted for Phases 1 or 2. The scope is too small and the architecture is
still being validated against real photos.

**Phase 3 (PWA frontend) is where outside contributions become welcome.**
If you want to contribute, open an issue to discuss before starting work.

---

## Running Locally

See [README.md](README.md) for setup and run instructions.

---

## Branch Naming

```
feature/<short-description>    New functionality
fix/<short-description>        Bug fix
docs/<short-description>       Documentation only
```

Examples: `feature/moment-grouping`, `fix/eye-openness-null-crash`,
`docs/api-contract`

---

## What a Good PR Looks Like

- Targets one thing. If you're fixing a bug and noticed a refactor opportunity,
  open two PRs.
- Includes a description of *why*, not just *what*. "Changed X to Y because Z"
  is more useful than "Updated scorer.py."
- Has been tested against real photos, not just synthetic data. Mention the
  test conditions (number of photos, subject type, profile used).
- Does not break the score breakdown. The `score_breakdown` field in the output
  is part of the contract — every axis must appear with `raw`, `weight`,
  `effective_weight`, `contribution`, and `source`.
- Does not add a new dependency without a clear reason. The Pi has limited RAM.

---

## Adding a New Scoring Profile

Profiles live in `phase1/ranker.py` in the `PROFILES` dict.

Rules:
1. All six axes must be present: `sharpness`, `exposure`, `eye_openness`,
   `expression`, `composition`, `subject_focus`.
2. Weights must sum to exactly 1.0 (the validator enforces this at runtime —
   but check it yourself first).
3. Include a comment in the PR explaining the intended use case and why the
   weights are set as they are. "landscape: de-emphasises eye_openness since
   no faces expected" is the right level of explanation.
4. Test the profile against at least one real photo set before submitting.

```python
"landscape": {
    "sharpness":     0.30,
    "exposure":      0.25,
    "composition":   0.25,
    "subject_focus": 0.15,
    "expression":    0.05,
    "eye_openness":  0.00,
},
```

Note: `eye_openness` can be 0.0 for profiles where faces are not expected.
Weight redistribution for `null` values still runs but adds nothing.

---

## Swapping the Vision Model

The semantic scoring abstraction point is `score_photos()` in
`phase1/scorer.py`. Any replacement vision model must satisfy this contract:

**Input:** `list[str | Path]` of image paths (sharp images only)

**Output:** `list[dict]` where each dict contains:
```json
{
  "photo_id":     "IMG_4821.jpg",
  "expression":   8,
  "composition":  6,
  "subject_focus":9,
  "notes":        "one specific actionable sentence"
}
```

Rules that must hold regardless of which model is used:
- Scores must be integers (or floats) in [1, 10]
- `notes` must be one sentence, specific and actionable
- All four keys must be present for every photo in the input
- On failure, raise — do not return partial results or default scores
- Never ask the model to score sharpness or exposure (measured deterministically)

If you add a new backend (e.g. Claude, GPT-4o), implement it as a separate
function alongside `score_photos()` and wire it in via a `--model` flag in
`ranker.py`. Do not modify the existing Gemini implementation.

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
   external service.

5. **Training consent is explicit only.** If any future version collects data
   for model training, it must be behind an explicit opt-in (`training_consent
   boolean` in the user table). Never infer consent from usage. Never train on
   data from users who have not opted in. This rule applies from the first
   public release — retrofitting consent is not acceptable.

PRs that violate any of these rules will not be merged.
