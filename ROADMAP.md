# PhotoRank Roadmap

Each phase has an explicit gate. The next phase does not start until the gate
condition is met and confirmed on real photos.

---

## Phase 1 — CLI Pipeline ✓ COMPLETE

**Goal:** A working, quality-verified scoring pipeline that can be run from the
terminal on a folder of real phone photos.

**Two modes (both ship in Phase 1):**

**`--mode burst`** — for 2–6 near-identical photos from the same moment.
Gemini is skipped entirely — no API cost, no network latency.
Signals: full-image sharpness and exposure + face-region sharpness and exposure
(OpenCV Haar cascade crop). Face-region signals catch per-face focus differences
that full-image sharpness misses. Fast enough for immediate feedback.

**`--mode set`** (default) — for larger or more varied photo sets.
Full two-layer pipeline: deterministic pre-filter (blur gate) then Gemini
semantic scoring (expression, composition, subject_focus, camera_engagement).
Gemini is only called on photos that pass the blur gate.

**In scope:**
- `core/score_tech.py` — deterministic scorer (sharpness, exposure)
- `core/score_burst.py` — face-region scorer for burst mode
- `core/score_vision.py` — Gemini semantic scorer (set mode only)
- `core/rank.py` — mode-aware pipeline, profile weights, CLI output
- Four built-in profiles: family, portrait, event, travel
- Custom profile via `--weights` JSON flag (set mode)
- Adjustable blur gate threshold
- Full score breakdown in output (per-axis scores, weights, sources, notes)

**Out of scope:**
- Any web server, API, or UI
- Batch/bulk photo handling beyond what fits in a folder
- Moment grouping

**Definition of done:**
- [x] Burst mode: run against at least three real burst sets (2–6 photos). Top pick
      agrees with human's top pick in >80% of test sets
- [x] Set mode: run against at least three varied sets (7–20 photos). Top pick
      agrees with human's top pick in >80% of test sets
- [x] Score breakdown explains the ranking in a way that makes sense to a
      non-technical reviewer
- [x] Exposure scoring correctly penalises blown highlights and underexposed shots
- [x] LEARNINGS.md updated with anything surprising from real-photo testing

**Phase 1 gate:** ✓ Passed — both modes validated against real photos. Phase 2 may begin.

---

## Phase 2 — FastAPI + Raspberry Pi Deployment ✓ COMPLETE

**Goal:** The Phase 1 pipeline accessible from a phone via a URL.

**In scope:**
- [x] FastAPI app wrapping Phase 1 logic (`api/main.py`)
- [x] `POST /rank` endpoint — multipart upload, returns scored JSON
- [x] `GET /health` endpoint
- [x] Immediate file deletion (try/finally, before response is dispatched)
- [x] EXIF stripping before images are sent to Gemini (cv2 strips via ingest)
- [x] Auto burst-mode detection from EXIF timestamps
- [x] Cloudflare Tunnel setup → `photorank.job-joseph.com`
- [x] Cloudflare Access gate (email-based auth)
- [x] Systemd service (auto-restarts on reboot)
- [x] `.env` on Pi for `GEMINI_API_KEY`

**Out of scope:**
- Any frontend
- User accounts or persistent sessions
- Batch/bulk ranking

**Definition of done:**
- [x] Upload photos from an iPhone, get ranked JSON back, in <60 seconds for 20 photos
- [x] Verified that files are deleted before response — confirmed with filesystem check
- [x] API accessible over Cloudflare tunnel; returns 401 for unauthenticated requests
- [x] Survives a Pi reboot (systemd service auto-restarts)
- [x] `GET /health` returns 200 and correct `gemini_key_set` value

**Phase 2 gate:** ✓ Passed — all items confirmed on real device. Phase 3 may begin.

---

## Phase 3 — Mobile-First PWA ✓ BUILT (gate pending device validation)

**Goal:** A usable, fast UI that runs in a phone browser. No App Store required.

**In scope:**
- [x] React + Vite PWA in `frontend/` (state machine in `src/App.jsx`; screens
      and components under `src/screens/` and `src/components/`)
- [x] Mobile-first layout (390px base, centred up to 480px on larger screens)
- [x] Photo upload: file picker + drag-and-drop, 2–20 validation with inline error
- [x] Profile selector (family / portrait / travel / event)
- [x] Three screens: upload, loading (animated pipeline stages), results
- [x] Hero №1 result + runners-up with expandable per-axis score breakdown
      (bar width = contribution, explicit math, dashed axis-max cap) rendered
      dynamically from the API `score_breakdown` (both burst and set modes)
- [x] Client-side image compression before upload (~1.5 MP, JPEG q0.85;
      HEIC→JPEG so previews render) — moved into the frontend
- [x] PWA manifest (add to home screen) + service worker (offline app shell;
      never caches `/rank`)
- [x] Graceful error/offline states — friendly copy, never raw server detail

Auth note: Cloudflare Access redirect is handled by the tunnel transparently;
the frontend makes no auth-specific code changes.

**Out of scope:**
- User accounts, saved history, or persistent rankings
- Moment grouping or full-day bulk cull
- Native app (App Store / Play Store)

**Definition of done:**
- [ ] Golden path works on iPhone Safari: pick photos, select profile, get ranked results
- [ ] Score breakdown is readable and makes sense to a non-technical user
- [ ] Notes are displayed prominently — not buried
- [ ] Upload + score + display completes in <90 seconds for 20 photos on a phone
- [ ] Passes Lighthouse PWA audit (installable)
- [x] Works offline with a sensible error state (not just a blank screen)

Build verified locally (`npm run build`), breakdown bar math validated against
SPECS §5.3, and the served bundle/manifest/SW/icons confirmed. The remaining
checkboxes require testing on a real device.

**Phase 3 gate:** End-to-end test on a real phone. At least one non-technical
person completes the full flow without assistance.

---

## Future — Not Scoped

These are being tracked but have no timeline or design yet. They do not
influence the current architecture.

**v2 — Full-day bulk cull (Archetype 2)**
Get from 200 photos to a post-ready shortlist. Requires moment grouping
(temporal clustering or visual similarity), best-of-group selection, and a UI
designed for a longer session, not just a quick burst review.

**Native apps**
React Native wrapper for iOS and Android. Likely post-v2.

**Desktop app**
Electron or Tauri for photographers who shoot on a camera and import to a
laptop. Different import flow, potentially higher-res images, different
compression needs.

**Custom preference model**
A lightweight model trained on consented user selection history — learns
individual aesthetic preferences over time. Requires explicit opt-in consent
(`training_consent boolean`). Must never train on data from users who have
not opted in.

**Instagram-aware scoring**
A profile tuned for carousel selection: composition and subject focus weighted
for square/portrait crop, expression still primary. Needs research into what
actually performs on Instagram.

**Natural language queries via Claude API**
"Find the best shot from the beach" or "Show me the ones where she's laughing."
Requires moment metadata and a retrieval layer — significant scope expansion.
