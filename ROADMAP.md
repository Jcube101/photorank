# PhotoRank Roadmap

Each phase has an explicit gate. The next phase does not start until the gate
condition is met and confirmed on real photos.

---

## Phase 1 — CLI Pipeline (current)

**Goal:** A working, quality-verified scoring pipeline that can be run from the
terminal on a folder of real phone photos.

**In scope:**
- `blur_filter.py` — deterministic technical scorer (sharpness, exposure, eye openness)
- `scorer.py` — Gemini 1.5 Flash semantic scorer (expression, composition, subject focus)
- `ranker.py` — two-layer merge, profile weights, CLI output
- Three built-in profiles: family, portrait, event
- Custom profile via `--weights` JSON flag
- Adjustable blur gate threshold
- Full score breakdown in output (per-axis scores, weights, sources, notes)

**Out of scope:**
- Any web server, API, or UI
- Batch/bulk photo handling beyond what fits in a folder
- Moment grouping

**Definition of done:**
- [ ] Run against at least three real burst sets (5–20 photos each) on different subjects
- [ ] Tool's top pick agrees with human's top pick in >80% of test sets
- [ ] Score breakdown explains the ranking in a way that makes sense to a non-technical reviewer
- [ ] Eye openness correctly identifies blinks and penalises them
- [ ] Exposure scoring correctly penalises blown highlights and underexposed shots
- [ ] LEARNINGS.md updated with anything surprising from real-photo testing

**Phase 1 gate:** All definition-of-done items confirmed before any Phase 2 code is written.

---

## Phase 2 — FastAPI + Raspberry Pi Deployment

**Goal:** The Phase 1 pipeline accessible from a phone via a URL.

**In scope:**
- FastAPI app wrapping Phase 1 logic (`api/main.py`)
- `POST /rank` endpoint — multipart upload, returns scored JSON
- `GET /health` endpoint
- Immediate file deletion (try/finally, before response is dispatched)
- EXIF stripping before images are sent to Gemini
- Cloudflare Tunnel setup (exposes the Pi endpoint publicly)
- Cloudflare Access gate (simple email-based auth)
- Systemd service to keep the API running on the Pi
- `.env` on Pi for `GEMINI_API_KEY`

**Out of scope:**
- Any frontend
- User accounts or persistent sessions
- Batch/bulk ranking

**Definition of done:**
- [ ] Upload photos from an iPhone, get ranked JSON back, in <60 seconds for 20 photos
- [ ] Verified that files are deleted before response — confirmed with filesystem check
- [ ] API accessible over Cloudflare tunnel; returns 401 for unauthenticated requests
- [ ] Survives a Pi reboot (systemd service auto-restarts)
- [ ] `GET /health` returns 200 and correct `gemini_key_set` value

**Phase 2 gate:** All definition-of-done items confirmed on real device before Phase 3.

---

## Phase 3 — Mobile-First PWA

**Goal:** A usable, fast UI that runs in a phone browser. No App Store required.

**In scope:**
- React PWA — `frontend/App.jsx`
- Mobile-first layout (designed for a 390px viewport, works up to desktop)
- Photo upload: file picker + drag-and-drop
- Profile selector
- Results view: ranked list with score, rank, breakdown toggle, and notes
- Client-side image compression before upload (max 1600px longest dimension,
  JPEG quality 80) — this is Phase 4 in the build sequence but naturally
  belongs in the frontend
- Progressive Web App manifest (add to home screen)
- Cloudflare Access login redirect handled transparently

**Out of scope:**
- User accounts, saved history, or persistent rankings
- Moment grouping or full-day bulk cull
- Native app (App Store / Play Store)

**Definition of done:**
- [ ] Golden path works on iPhone Safari: pick folder, select profile, get ranked results
- [ ] Score breakdown is readable and makes sense to a non-technical user
- [ ] Notes are displayed prominently — not buried
- [ ] Upload + score + display completes in <90 seconds for 20 photos on a phone
- [ ] Passes Lighthouse PWA audit (installable)
- [ ] Works offline with a sensible error state (not just a blank screen)

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
