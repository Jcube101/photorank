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

**Discovery:** Face-region Laplacian variance (face_blur_raw) shows 3x difference between best and worst photo in a true burst set where full-image sharpness showed less than 6% variance.
**Impact:** Burst mode validated. Face crop is the correct unit of measurement for burst differentiation, not full image. face_sharpness at 0.5 weight is the primary signal for burst ranking. This approach is fast, free, and more accurate than Gemini for this use case.
**Date:** 2026-05-16

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

**Discovery:** The Claude Design prototype encoded a simplified 5-axis mock model (sharpness, expression, composition, exposure, focus) that does not match the real API's 6-axis `score_breakdown` (and burst mode's 4 face/full-image axes).
**Impact:** The production PWA renders the breakdown **dynamically from whatever axes the API returns**, never hardcoding a weight table. One code path (`frontend/src/lib/breakdown.js`) handles both modes: zero-weight axes (e.g. `camera_engagement` outside `family`) render muted, and burst-mode face axes render unchanged. Design files were extracted for tokens/layout only, then archived to `frontend/_design/`.
**Date:** 2026-05-31

---

**Discovery:** Browsers other than iOS Safari cannot decode HEIC, so a HEIC `<img>`/`createImageBitmap` preview silently fails — and HEIC is the iPhone default.
**Impact:** Client-side compression does double duty: downscaling to ~1.5 MP for upload **and** re-encoding to JPEG via `<canvas>`, which yields a browser-renderable preview. The original filename is preserved so the server's `photo_id` maps back to the right preview. On decode failure the original file is uploaded untouched and the UI falls back to the design's gradient placeholder — scoring still works, only the thumbnail is generic.
**Date:** 2026-05-31

---

**Discovery:** `POST /rank` is a single blocking call, so the frontend cannot show real per-stage pipeline progress.
**Impact:** The loading screen animates the four backend stages cosmetically — the bar eases toward ~92% while the request is in flight and completes when the response lands. The Gemini "Scoring axes" step is hidden when ≤6 files are selected as a burst guess, but `mode` is omitted from the request so the **server** still makes the authoritative burst/set decision from EXIF. The displayed stages are honest about the pipeline shape without implying live telemetry.
**Date:** 2026-05-31

---

**Discovery:** "Bar width = raw score" misrepresents how a photo actually ranks; what matters is each axis's *contribution* to the weighted total.
**Impact:** Breakdown bars are drawn as **contribution** (weight × raw), normalised so the heaviest axis at a perfect 10 fills the track, with a dashed cap marking that axis's own maximum. Each row shows the explicit math (`raw × weight = contribution`). This makes a high-raw-but-low-weight axis visibly small, matching its real influence on the rank — reinforcing the "verify, don't trust" principle.
**Date:** 2026-05-31

---

**Discovery:** The blur gate rejecting all-soft batches looked like a 2-photo minimum bug because most 3+ photo sets have at least one sharp frame. Root cause was both photos scoring `blur_raw` 70.6, below the 100 threshold — so the gate excluded both, leaving nothing to rank, and `/rank` returned 422. A 2-shot burst is far more likely to be uniformly soft than a larger set, which is exactly why "2 fails, 3+ works" appeared count-driven when it was really content-driven.
**Impact:** The blur gate now falls back to ranking all photos when every photo is soft, rather than returning 422 — its job is to *thin* a set (drop soft frames when sharp ones exist), not to refuse to rank. The response carries `blur_gate_bypassed: true` when this fires (transparency, no silent failure). Hard failure now only occurs on corrupt/unreadable files (no image scoreable at all). Fixed in both the API and the CLI, in both burst and set mode.
**Date:** June 2026

---

**Discovery:** Profiles are *only* weights — a set-mode run already scores every photo on all six axes regardless of profile (`camera_engagement` is even returned at weight 0 in profiles that don't use it), so switching profiles needs no re-scoring.
**Impact:** The results screen got an on-device profile switcher that re-ranks the existing result by pure client-side re-weight — instant, no re-upload, no Gemini call, works offline. `rerankByProfile()` mirrors `core/rank.py:rank_photos` exactly (same weighted sum, round-to-3, and `(final_score desc, relative_rank)` tiebreak); verified to reproduce the server's `final_score` to the digit. Two honest limits, surfaced in the UI: (1) `travel` is the only profile with a Gemini hint, so re-ranking *into* travel from another profile's raw scores is directional, not identical — flagged with an "approximate" note; (2) burst-mode results carry 4 face/full axes that don't map onto the 6-axis profiles, so the switcher is hidden for them.
**Date:** June 2026

---

**Discovery:** Android can kill a PWA's process during a tab switch, dropping the user back to the upload screen and losing their results.
**Impact:** The last result JSON is persisted to **`sessionStorage`** (not `localStorage`) under `photorank_last_result`, restored on load, and cleared on "New batch". `sessionStorage` clears when the tab closes, so it bridges a process kill *within* a live session without becoming durable history — consistent with the no-persistence privacy intent. Only scores/notes/photo_ids are stored, never pixel data. The full `score_breakdown` (incl. raw axis scores) is preserved, so the profile switcher keeps working after a restore. Image previews are `blob:` URLs that can't survive the kill, so a restored screen falls back to gradient placeholders and disables "Save winner" — the ranking, scores, and breakdowns remain fully intact.
**Date:** June 2026

---

**Discovery:** Showing a greyed-out 0% row for axes a profile doesn't weight (e.g. `camera_engagement` outside `family`) confuses more than it informs — a row that contributes nothing reads as a bug.
**Impact:** `buildBreakdown` filters axes to `weight > 0`, so the breakdown shows only axes that actually move the score. It's dynamic: as the profile switcher changes weights, axes appear/disappear accordingly. Total and bar scaling are unaffected (zero-weight axes contribute 0 and have a zero cap).
**Date:** June 2026

---

**Discovery:** Gemini batches were running sequentially. 20 photos = 3 batches × ~40s each = 125s total. The batches are network-bound and independent (each scores its own ≤8 photos, no cross-batch dependency).
**Impact:** A `ThreadPoolExecutor` in `score_vision.py` runs the batches concurrently, collapsing total Gemini time to the slowest single batch (~37–47s) instead of their sum. Pipeline total dropped from ~130s to 37–52s server-side. Confirmed **59 seconds end-to-end on a Samsung phone via the PWA** (includes Cloudflare tunnel latency + client-side compression). No scoring logic changed: same prompts, batch size, and parsing; results are reassembled in batch order so output stays deterministic, and a single batch skips the pool. Concurrency is capped by `MAX_CONCURRENCY` (default 4) to stay within rate limits.
**Date:** June 2026

---

*Add new entries above this line as discoveries are made.*
