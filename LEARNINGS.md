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

**Discovery:** For a same-domain FastAPI setup (frontend and API served from the *same* hostname), Cloudflare Access can protect the entire domain — including API endpoints like `/rank` — with no application code changes.
**Impact:** PhotoRank serves the PWA and the API from `photorank.job-joseph.com`, so requests are same-origin: there are no CORS preflights, and Access at the edge can gate everything (24h email-OTP session) without breaking the API. The well-known caveat — that Access blocks API calls because a CORS `OPTIONS` preflight doesn't carry the Access session cookie/JWT and gets bounced to the login page — only applies when the frontend and API live on *different* subdomains. Configured via `~/projects/dev-meta/cf_access_setup.py`.
**Date:** 2026-06-24

---

**Discovery:** Gemini 3 models think by default, and thinking adds latency to every call. Scoring is a structured rating task against a fixed rubric, so extended reasoning costs time without a clear gain. The old `google-generativeai` SDK (0.8.x) also has no thinking config at all, so it could not turn this down.
**Impact:** Moved to `gemini-3.8-flash` and migrated `score_vision.py` to the `google-genai` SDK. Every scoring call pins thinking to `low` via `GEMINI_THINKING_LEVEL` (default `low`). Prompts, weights and the JSON response schema are unchanged. `response.text` excludes thought parts, so parsing is unaffected. The latency figures above came from 2.0 Flash; re-measure on the Pi (target: 20 photos under 60s server time).
**Date:** 2026-10-03

---

**Discovery:** The Sony A7 III writes no sub-second capture time. None of the 187 Wat Arun JPGs has `SubSecTimeOriginal`, `SubSecTime` or `SubSecTimeDigitized`. They do all have `DateTimeOriginal` and `OffsetTimeOriginal`. This answers V2-PLAN Q19. On this shoot, 29 of 186 adjacent pairs are ≤ 1 s apart, and one pair shares a timestamp.
**Impact:** Every photo is `time_source: "exif"` at 1 s resolution. Ordering inside one second falls back to the filename tiebreak in `order_photos`, which matches DSC order here. `dt` is a whole number of seconds, which is fine for the 20 s / 90 s thresholds. The grouping-only pass also showed (before any labels) that time does all the work on this shoot. Every proposed boundary has `dt ≥ 20 s`, and every pair ≥ 40 s apart is one. Rule 4 never fires: `dc` tops out at 0.30 for quick pairs, below `C_HIGH` 0.35, so with `FAST_RULE=and` a quick reframe can never split (V2-REVIEW G3). Eleven quick pairs have `dh ≥ 24`. The answer key will show whether they are real reframes.
**Date:** 2026-11-25

---

**Discovery:** On a professional shoot, face sharpness alone is a weak picker. On Wat Arun (Sony A7 III, 85mm f/1.4, oracle grouping, 3 runs) `face_sharpness` alone put the owner's pick first in 17–27% of sets, about the 23% that random choice gets. The photographer nailed focus, so frames within a set differ in expression and pose, not sharpness. Gemini's axes alone reached 43–46%, the combined weights 42–46%. Round weight sets between those barely differ: every sensible one scored 44–47% top-1 and 78–82% top-3, inside Gemini's own run-to-run range. The weight sweep's best (59% top-1, on scores averaged over runs) has no plateau around it, so it is a fit to noise on about 34 sets.
**Impact:** The plan assumed missed focus is the most common reject at f/1.4. For a pro shoot it is not: expression and pose decide picks. `face_sharpness` stays as a small guard for shoots where focus does miss; 0.1 does as well as 0.3 here. The owner's pick is in the model's top 3 in about 81% of sets but first in under half, so the results UI shows each set's top 3 (V2-PLAN §10.3).
**Date:** 2026-11-26

---

**Discovery:** Gemini can write a photo's position into its filename. Shoot mode labelled each photo "Photo N — photo_id: <filename>". For the set DSC00005–DSC00009 (photos 1–5), Gemini returned `DSC00003.JPG` and `DSC00004.JPG` for photos 3 and 4, in all 6 evaluation runs and again in a diagnostic call. The set was then missing two photos and failed. That check sat outside the retry loop, so it failed at once. Two other sets failed intermittently (4 of 6 and 1 of 6 runs). Their error text was not kept, so their cause is unconfirmed.
**Impact:** Shoot mode now labels photos `photo_1` … `photo_N`, so the label is the position and cannot be confused with it. It maps replies back by label, rewrites labels in notes to filenames, and retries a reply that names the wrong photos (`strict_ids`). Checked live: both sets that had failed then scored on the first request, spread off and on (4 of 4 calls, no retries). Set mode then still labelled by filename and, on a mismatch, silently dropped the photo from the ranking while `api/main.py` logged its filename (privacy rule 2). Fixed the same day: `score_photos` labels every batch by position in every mode and raises rather than return fewer photos, the API fails the request (502) instead of dropping a photo, and neither the log line nor the 502 detail carries a filename or Gemini's raw reply.
**Date:** 2026-11-26

---

**Discovery:** On Wat Arun every grouping error with the default thresholds was a missed boundary between frames under 20 s apart: 6 of 35 boundaries, at gaps of 9–18 s, while frames inside a set are about 2 s apart. Lowering `T_SOFT` to 15 s (with `H_LOW` 10 and `C_LOW` 0.15) reaches 3 corrections, the §12.2 target. The plan's adaptive time term (a pause is 4 × the recent in-set pace) also reaches 3. It fixes a different four of the six and adds a different false split, so on one shoot with a steady pace it does not beat the fixed rule.
**Impact:** The adaptive rule is kept as an option (`TIME_RULE`, off by default) until a shoot with a varying pace is labelled. The previous sweep grid (`T_SOFT` 10/20/30) stepped over 15 s, so it now includes 15.
**Date:** 2026-11-26

---

**Discovery:** On Android Chrome every fresh result showed the gradient placeholder instead of the photo, in every mode and batch size. The API rewrote each upload's name before using it as `photo_id`: it lower-cased the extension and added `.jpg` when there was none. The PWA finds each preview by the name it uploaded, so any name that was not already lower-case `.jpg` missed. Reproduced in headless Chromium: `1000330417.JPG` and a bare `1000330417` both came back as `1000330417.jpg`, and every card showed the placeholder; lower-case `.jpg` worked, which is why desktop and iPhone testing passed. The on-screen label is CSS-uppercased, so it cannot show which case the server returned. The rewrite dates from Phase 2 (`1e7e803`); it broke previews from the first PWA commit (`35f6111`), which keyed them by the original name. `910cceb` blamed the same symptom on the service worker. A repeated filename had a second problem: the PWA's preview map kept only one of the two, so one card showed the other photo.
**Impact:** `photo_id` is now the uploaded filename exactly (SPECS §6.1). The PWA gives each upload a unique, non-empty name first, so the server never renames anything it sends. `tests/test_results_photos_e2e.py` drives the real flow in headless Chromium with Android-style names and checks that each card shows its own photo, and that a result restored from sessionStorage still shows placeholders.
**Date:** 2026-11-27

---

**Discovery:** A wrong cause, declared without reproducing, let the real bug survive for five months. On 2026-06-21, commit `910cceb` saw previews showing as placeholders in browser Chrome and blamed the service worker, saying it intercepted `blob:` URLs. It added a bypass for them. But a service worker never sees `blob:` requests, so the change could not have fixed anything. The real cause was the server renaming `photo_id` (entry above), and it stayed broken until 2026-11-27. Nobody had uploaded the file names the failing device produces: desktop and iPhone names were already lower-case `.jpg`, so every test passed. The bug reproduced at the first attempt once the upload used Android-style names (`.JPG`, no extension) in headless Chromium with Android emulation.
**Impact:** Before declaring a cause, reproduce the bug under the target device's conditions: its browser, its file names and formats, and its flow. Then show that the fix makes the reproduction pass. A fix with no failing reproduction behind it is a guess. Every phone-reported bug now gets a test that fails on the old code (`tests/test_results_photos_e2e.py` drives headless Chromium on an emulated Android phone).
**Date:** 2026-11-27

---

**Discovery:** Burst mode never triggered from the app. Two faults each blocked it on their own. (1) The API read `DateTimeOriginal` (0x9003) from IFD0, but phones write it in the Exif sub-IFD (0x8769), so even original uploads had no time. Every test photo in `input/` has it only there. (2) The PWA re-encodes every photo through `<canvas>` before upload, which strips all EXIF, so the server had nothing to read anyway. With a time missing, detection falls back to set mode, so every 4-photo burst from the app ran set mode, with Gemini, since Phase 3.
**Impact:** The PWA now reads each original's `DateTimeOriginal` on the phone before compressing. `src/lib/captureTime.js` reads only the first 128 KB of a JPEG and only that one tag, and never follows the GPS pointer. It sends the times as a `capture_times` field keyed by upload name (SPECS §6.1). The server uses the sent time, else the file's own EXIF, now read from the Exif IFD by `core/group.py:read_capture_time`. HEIC originals still send no time and run set mode. Tests: `tests/test_capture_time.py` covers the phone reader in both byte orders, plus a GPS pointer it must never follow. The API and browser tests check that 4 photos within 10 s run burst mode with no Gemini call.
**Date:** 2026-11-27

---

**Discovery:** Burst results on the owner's Android phone showed the gradient placeholder on every card. The preview lookup was fine: the phone never made previews. Each request's ingest time in the server log scales with pixel count, so it shows what was uploaded without logging any size: the burst run took 0.18 s a photo, like 12 MP originals on the Pi (0.21 s) and six times a 1.5 MP compressed upload (0.03 s). So `compressImage` failed for every photo and uploaded the original with no preview. The day's two set-mode runs uploaded originals too but failed on Gemini (a revoked API key) before showing results, so the bug looked burst-only. "Save winner" looked enabled, but a disabled `.btn-primary` has no style of its own, so that was no evidence of a preview. Earlier runs from the same phone were compressed. Headless Chromium decodes the same real photos, so the failure is not reproduced, and why the phone's `createImageBitmap` rejects them is not known.
**Impact:** `compress.js` decodes through an `<img>` when `createImageBitmap` rejects, and if re-encoding still fails it previews the original; the cards (`components/Photo.jsx`) show the placeholder only when the image won't load. `tests/test_results_photos_e2e.py` makes Chromium's decoders reject the photos, as the phone's did, in a real 5-photo burst. An uploaded original carried its full EXIF, GPS included, to the Pi. Closed the same day: see the next entry.
**Date:** 2026-11-27

---

**Discovery:** Every card in a burst result said "Strongest on face sharpness in this set.", 3rd and 4th place included. The note named the photo's own largest contribution, without comparing it with the other photos. Face sharpness carries 50% of the burst weight, so it was the largest on nearly every card.
**Impact:** Notes compare across the result (`src/lib/notes.js`, SPECS §9.4): only №1 gets a superlative, and every other photo is described against №1, at the one decimal the breakdown shows. `tests/test_notes.py` checks each claim against the scores on 300 random bursts; the browser test checks a real burst.
**Date:** 2026-11-27

---

**Discovery:** When compression failed, the PWA uploaded the original photo as-is, with its full EXIF (GPS included), XMP, ICC profile and any trailer, although the privacy rule said only the capture time leaves the phone. The server stripped it at ingest and deleted the file, but it had already left the phone. The owner's phone hit this path on its first burst runs. A Samsung camera JPEG holds EXIF with GPS (APP1), an ICC profile (APP2), an APP4 segment and data after the image; Motion Photos add a whole video there.
**Impact:** An original is never uploaded as-is. A JPEG has every metadata segment cut out of its bytes without decoding (`src/lib/stripJpeg.js`), using an allowlist of what a decoder needs, so metadata we don't know about goes too. On real Samsung files the pixels are identical afterwards. Anything that can't be cleaned that way (e.g. HEIC on Android Chrome) is not uploaded, and the app names it. Stripping drops the orientation tag as well, so in this fallback a rotated photo is scored and shown sideways. Tests: `tests/test_strip_jpeg.py`; the browser test forces compression to fail on photos carrying GPS, XMP, IPTC, a comment and a trailer, and checks the server gets none of it.
**Date:** 2026-11-27

---

*Add new entries above this line as discoveries are made.*
