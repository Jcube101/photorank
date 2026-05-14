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

*Add new entries above this line as discoveries are made.*
