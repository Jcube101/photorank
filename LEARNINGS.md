# PhotoRank — Learnings

A living document. Each entry records a discovery, its impact on the approach,
and when it was found. Update this whenever something real-photo testing or
implementation reveals surprises or forces a course correction.

---

**Discovery:** Gemini alone cannot reliably differentiate near-identical burst shots.
**Impact:** Split scoring into two layers. Deterministic OpenCV scoring (Laplacian +
Tenengrad + histogram + MediaPipe EAR) handles all objective technical quality axes.
Gemini is restricted to the three semantic axes it can actually judge reliably:
expression, composition, and subject_focus. Sharpness and exposure are excluded from
the Gemini prompt entirely.
**Date:** 2026-05-14

---

**Discovery:** Laplacian variance alone is fooled by high-contrast out-of-focus shots.
A bright edge against a dark background produces a high Laplacian reading even when
the image is clearly blurry.
**Impact:** Combined Laplacian variance with Tenengrad gradient energy. The two
measures disagree on out-of-focus high-contrast shots. Averaging them eliminates the
false-positive case. Both are log-normalized independently before averaging so neither
dominates.
**Date:** 2026-05-14

---

**Discovery:** Scoring must be relative within a set, not absolute across all photos.
A score of 7 means nothing without knowing the range of scores in the batch.
**Impact:** Output includes `final_rank` alongside `final_score`. The primary user
action is "pick the top 1–3 from this set", not "compare this photo against photos
from other sessions". The UI should foreground rank over raw score.
**Date:** 2026-05-14

---

**Discovery:** Transparency (score breakdown + Gemini notes) is the core feature,
not just the ranking.
**Impact:** Every output includes a full per-axis breakdown showing raw score, weight,
effective weight (post-redistribution if eye_openness is null), and source layer.
The Gemini notes field is one specific, actionable sentence — not a generic summary.
The UI must never show only a final number. Users need to be able to verify the
ranking, not just trust it.
**Date:** 2026-05-14

---

**Discovery:** Client-side image compression is required before upload on mobile.
**Impact:** Raw phone photos are 3–15 MB each. A 20-photo set at full resolution
would exceed typical mobile upload budgets and slow the pipeline significantly.
Compression to max 1600px / JPEG 80 is planned for Phase 4. Until then, the CLI
works on local files where network is not a constraint. The Phase 3 PWA must not
ship without compression.
**Date:** 2026-05-14

---

**Discovery:** Photos must never be persisted — this is a hard constraint, not a
nice-to-have.
**Impact:** No database, no cache, no temp file that outlives the request. In Phase 2,
a try/finally block in the FastAPI route handler ensures deletion runs even if scoring
fails. EXIF data (which includes GPS coordinates) must be stripped before images are
sent to Gemini. This constraint shapes the entire backend architecture: no session
history, no re-scoring, no "here are your results from last time."
**Date:** 2026-05-14

---

**Discovery:** MediaPipe is not available for Raspberry Pi ARM64. The official
package does not ship ARM64 wheels, and community builds (mediapipe-rpi4) are
unmaintained and incompatible with current Python versions.
**Impact:** Eye openness detection is stubbed at a neutral 5.0 until a
Pi-compatible alternative is found. Candidates: dlib shape predictor (requires
a compiled .dat model file), InsightFace (heavier but has ARM builds),
or a lightweight OpenCV Haar cascade for eye detection. Profiles that heavily
weight `eye_openness` (portrait: 0.25, family: 0.20) will produce less
differentiated results until this is resolved.
**Date:** 2026-05-15

---

**Discovery:** Sharpness normalization ceilings were too low for real phone photos at 1.5 MP.
**Impact:** All 11 test photos scored 10.0 on sharpness, providing zero differentiation. The
original ceiling of Laplacian 400 / Tenengrad 2500 was calibrated for lower-resolution images.
Sharp phone photos at 1.5 MP routinely exceed those thresholds. Raised to Laplacian 2000 /
Tenengrad 12000 to restore meaningful spread. Added `tenengrad_raw` to the output dict so raw
values are visible for future calibration.
**Date:** 2026-05-15

---

**Discovery:** eye_openness stub returning 5.0 for every photo is dead weight, not neutral.
**Impact:** A constant 5.0 across all photos contributes nothing to differentiation but still
consumes 20–25% of the profile weight (depending on profile). Removed eye_openness from all
profiles entirely. Original weights redistributed proportionally: family expression 0.25→0.31,
portrait sharpness/expression both 0.20→0.27, event composition 0.25→0.28. The axis will be
re-added when a Pi ARM64-compatible implementation is found.
**Date:** 2026-05-15

---

**Discovery:** Gemini consistently fails to differentiate true burst shots (same moment, <10 seconds apart, near-identical framing).
**Impact:** Scores cluster identically across all photos in a burst regardless of prompt engineering.
Vision LLMs are not suitable as the primary differentiator for true bursts — the visual difference
between two burst frames is below Gemini's perceptual resolution. Two-mode architecture introduced:
`--mode burst` uses deterministic face-region scoring only (sharpness and exposure on the face crop,
not the full image); `--mode set` uses deterministic pre-filter + Gemini for semantic scoring.
Face-region signals matter because full-image sharpness can be identical across a burst even when
one frame has the face perfectly sharp and another has it slightly soft.
**Date:** 2026-05-16

---

**Discovery:** Expression quality and camera engagement are two separate signals.
**Impact:** A subject can have a pleasant expression while looking away from the camera — which ruins
a family photo but would not be caught by expression scoring alone. Added `camera_engagement` as an
explicit Gemini axis with a strict scoring rule enforced in the prompt: any subject not looking at
the camera caps the score at ≤6; all subjects at camera starts at 8. The axis carries weight 0.20 in
the family profile and 0.00 in all others (scored always, only weighted in family).
**Date:** 2026-05-16

---

**Discovery:** For multi-person shots, averaging expression across subjects masks individual weaknesses.
**Impact:** A single person with closed eyes or a flat expression ruins the photo regardless of how
good the other subject looks. Simple averaging lets a strong expression on one person mask a blink or
flat look on the other. New multi-subject expression schema: Gemini returns `subject_1_expression` and
`subject_2_expression` independently. Python computes `expression = lower * 0.65 + upper * 0.35`,
biasing toward the weaker score. Gemini is also prompted to specifically assess: eyes fully open vs
partially closed; genuine smile vs neutral/forced; both subjects engaged vs one distracted.
**Date:** 2026-05-16

---

**Discovery:** Gemini clusters semantic scores in the 5–7 range when given no relative anchor.
**Impact:** Without explicit instruction to rank photos against each other, Gemini treats each
photo in isolation and gravitates to the middle of the scale. Added relative-ranking instruction
to the system prompt ("rank these against each other, not an abstract standard") with explicit
spread requirements (at least one 8+, at least one <5 per axis). Added a `relative_rank` field
(1 = best) that Gemini assigns as a holistic tiebreaker, used in rank.py when final_score values
are equal.
**Date:** 2026-05-15

---

*Add new entries above this line as discoveries are made.*
