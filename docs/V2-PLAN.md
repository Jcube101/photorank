# PhotoRank v2 — "Full shoot" Mode (Archetype 2)

**Status:** Draft design, not yet approved. No code is written for it yet.
**Author context:** Builds on CLAUDE.md, SPECS.md, ROADMAP.md, LEARNINGS.md as of 2026-10-03.
**Reference shoot:** Wat Arun, 180 JPGs, about 10 MB each, Sony A7 III, 85mm f/1.4, one subject.

---

## 1. Summary

Full shoot mode turns one long, ordered shoot (100–300 photos) into a list of
**sets**. Each set gets one **winner**, and the user sees why it won. The user
can fix wrong set boundaries, override a pick, and star winners to build a
shortlist.

The core idea in one paragraph: the phone reads each photo's capture time
**before** compressing it. During compression it also computes two small
visual fingerprints (a 64-bit difference hash and a colour histogram). Using
those, it finds **set boundaries between adjacent frames**. This is a linear
pass, not clustering. The phone then uploads compressed photos in chunks that
hold **whole sets only** to a new async job API on the Pi. The Pi scores each set
as soon as it arrives, using **both** face-region sharpness and Gemini (one
Gemini call per set, so Gemini's relative judgement stays inside one moment).
It deletes each set's photos as soon as that set is scored. The phone polls
for progress and fetches results as sets finish. Compressed copies stay in
IndexedDB on the phone so a killed tab can resume.

The v1 burst/set flow (`POST /rank`, 2–20 photos) is **not changed** in any
way.

---

## 2. What the reference shoot tells us

| Observation | Design consequence |
|---|---|
| Files are already in shooting order (DSC numbers). Sets are contiguous and never revisited. | Grouping is **boundary detection between neighbours** (N−1 decisions), not clustering. O(N), explainable, and every mistake is a single merge or split. |
| About 15–25 sets of 5–15 photos. | One Gemini call per set fits easily. About 20 calls per shoot. Chunks can be packed from whole sets. |
| 85mm f/1.4, shallow depth of field. | (a) **Face-region sharpness** is a real differentiator (a missed-focus eye). (b) **Full-image sharpness is misleading**: most of each frame is intentionally out of focus, so full-image Laplacian is low on perfectly good frames. The v1 blur gate (full-image `blur_raw < 100`) would wrongly drop sharp portraits. See §6.3. |
| About 10 MB per file, 180 files. | Raw is about 1.8 GB, which can't be uploaded or held in memory. After compressing to about 1.5 MP and JPEG q0.85, each file is roughly 300–500 KB, about 70 MB in total. Compression must be sequential and release memory each time. |
| One subject. | `subject_2_expression` will be `null`. `expression = subject_1_expression`. The two-subject logic stays in place but goes unused. |

---

## 3. Goals and non-goals

**Goals**
1. A separate "Full shoot" mode on the upload screen. The v1 flow stays as it is.
2. Capture time read on the phone from the original file, before compression.
3. Boundary detection from time gap plus cheap visual similarity. No embeddings, no CLIP.
4. Every set scored with **both** face-region sharpness and Gemini, at any set size.
5. Async job model (create, upload chunks, poll, fetch), with no request near the 100s Cloudflare limit.
6. Privacy rules unchanged in spirit. Photos are deleted on the Pi after scoring and never cached by the service worker. Compressed copies live in IndexedDB on the phone only.
7. A results UI with sets, winners, reasoning, override, stars, shortlist, merge, and split.
8. A clear "sign in again" path when the Cloudflare Access session expires.

**Non-goals (v2)**
- Clustering of out-of-order or multi-day libraries. Sets are assumed contiguous.
- Embeddings, CLIP, or any server-side similarity model.
- Accounts, server-side history, or cross-device sync.
- Uploading original-resolution files.
- Changing v1 profiles, `POST /rank`, or the v1 results screen.

---

## 4. End-to-end flow

```
PHONE                                                     PI (FastAPI :8007)
─────                                                     ──────────────────
1. User picks "Full shoot", selects 180 files
2. For each file, SEQUENTIALLY:
     a. read EXIF (DateTimeOriginal + SubSec) from first 128 KB
     b. decode → canvas ~1.5 MP → JPEG blob (EXIF gone)
     c. from the same canvas: dHash64 + HSV histogram
     d. write {blob, time, hash, hist} to IndexedDB, free memory
3. Sort by capture time (filename tiebreak)
4. Boundary detection → groups (instant, on-device)
5. POST /jobs  ───────────────────────────────────────────►  create job, return job_id
6. Pack whole groups into chunks (≤20 photos / ≤10 MB)
   POST /jobs/{id}/chunks  (repeat) ─────────────────────►  save chunk to tmp dir, 202
                                                            enqueue each group:
                                                              ingest → face scores →
                                                              Gemini (1 call/group) →
                                                              merge → rank →
                                                              DELETE group's files
7. POST /jobs/{id}/finalize ─────────────────────────────►  no more chunks expected
8. GET /jobs/{id} every 3s  ◄────────────────────────────  {groups_scored: 7/19, …}
9. GET /jobs/{id}/results?since=… ◄───────────────────────  newly scored groups
10. Results screen fills in group by group
11. DELETE /jobs/{id} on "New shoot" (else TTL reaper)
```

Boundary detection runs on the phone, not the Pi. Reasons:
- The phone already has everything it needs: the original EXIF (which the Pi never sees, because canvas compression strips it) and the decoded bitmap (so the fingerprints cost almost nothing).
- Groups exist **before** upload, so each chunk can hold whole groups. The Pi can then score and delete each group right away, without holding a 180-photo shoot on disk while it waits for the last chunk. This keeps on-Pi dwell time close to v1.
- Merge and split are local edits on data the phone already holds.
- The Pi only needs filenames and group membership. Capture times and fingerprints never leave the phone.

A Python reference version of the same algorithm (`core/group.py`) exists for
the CLI and the evaluation harness (§12). The two implementations are kept in
sync with a shared golden-fixture test (§7.6), the same way
`PROFILE_WEIGHTS` is mirrored today.

---

## 5. Phone-side ingest

### 5.1 EXIF capture time (before compression)

Canvas re-encoding strips EXIF, so time has to be read from the original
`File` first.

- Read only `file.slice(0, 131072)`. For JPEG, APP1/EXIF sits at the start of the file. This avoids pulling 10 MB into memory just for metadata.
- Tags used:
  - `DateTimeOriginal` (0x9003): 1-second resolution.
  - `SubSecTimeOriginal` (0x9291): sub-second part, if present. Needed to order frames shot in continuous drive.
  - `OffsetTimeOriginal` (0x9011): read but not needed. Only deltas between frames matter.
- Parser: a small hand-written APP1/TIFF IFD reader (about 100 lines), or `exifr`'s lite build with `pick` limited to these tags. **Recommendation:** use `exifr` lite. It also handles HEIC for phone-camera users. Decide during build (Open question Q9).
- **GPS and every other tag are never read or stored.**
- Fallback chain, recorded per photo as `time_source`:
  1. `exif_subsec`: DateTimeOriginal plus SubSec
  2. `exif`: DateTimeOriginal only (ties broken by filename)
  3. `file_mtime`: `file.lastModified` (unreliable after transfer; flagged in the UI)
  4. `none`: grouping falls back to visual-only (§7.4)
- **Ordering:** sort by `(captured_at_ms, filename)`. Filename alone is not enough because DSC counters wrap from 09999 to 00001. Time is primary.

### 5.2 Sequential compression

The existing `compressAll()` is already sequential. Full shoot mode reuses
`compressImage()` with these additions:

- Strictly one file in flight. `await` each one, call `bitmap.close()`, and set `canvas.width = canvas.height = 0` after `toBlob` so the GPU backing store is released.
- Where supported, use `createImageBitmap(file, { resizeWidth, resizeHeight, resizeQuality: "high" })` to decode straight to target size. This avoids a 24 MP (about 96 MB RGBA) intermediate. Feature-detect it and fall back to the current path.
- Compute fingerprints (§7.2) from the **same** canvas before releasing it, using a 64×64 downscale. This adds no extra decode.
- Write each result to IndexedDB immediately (§8.2), so progress survives a kill partway through.
- Show progress as "Preparing 37 / 180". Keep the screen on with the Screen Wake Lock API where available.
- **Resume after a kill partway through compression:** original `File` handles do not survive a process kill. On resume, the app shows "Re-select your photos to continue". It dedupes by `(name, size, lastModified)` and only compresses the missing ones.

### 5.3 Selection limits

- Full shoot accepts **2–300** files (proposed; Q2). Selection is **additive**: "Add more photos" appends and dedupes. This is a mitigation if a phone's picker caps a single selection (Q4).
- v1 keeps its 2–20 limit.

---

## 6. Scoring each group (server)

### 6.1 Pipeline per group

```
group files (tmp dir) → ingest() (cv2 re-encode, strips anything left)
  → compute_burst_scores() per photo     # sharpness, exposure, face_sharpness, face_exposure, raws
  → score_photos(group, batch_size=len(group), prompt=SHOOT)   # ONE Gemini call per group
  → merge → rank with SHOOT_WEIGHTS
  → store result in memory, DELETE group's files (try/finally)
```

- **Both** signal families run on every group of every size. There is no burst/set split inside full shoot mode.
- `compute_burst_scores()` already returns the full-image and face-region axes. Nothing new is needed here for Phase A.
- **Gemini batch = group.** `score_vision.BATCH_SIZE` (8) becomes a parameter. A group of up to `GEMINI_GROUP_MAX` (proposed 20) photos goes in one call, so `relative_rank` and the relative-scoring instruction compare frames **within one moment**. This is what Gemini is reliably good at, and it avoids comparing scores across batches. Groups larger than `MAX_GROUP` are split before upload (§7.3), so this cap is never exceeded.
- Groups are scored concurrently across the job, capped by the existing `MAX_CONCURRENCY` (default 4). CPU face scoring runs in its own small thread pool so it overlaps with network-bound Gemini calls.

### 6.2 Shoot-mode Gemini prompt

This is the same `_SYSTEM_PROMPT` contract with a short shoot-mode addendum. It never asks about sharpness or exposure.

> These photos are consecutive frames of a single set from one portrait
> shoot: the same subject, location and outfit. Rank them against each other.
> Focus on the differences that matter between frames of one set: expression,
> eyes, pose, hands, gaze, and framing.

**Open issue (Q6):** the current prompt forces spread ("at least one 8+, one
<5 per axis"). Inside a tight set this can invent differences. The evaluation
(§12.3) runs both variants. The default is chosen from the data, not assumed.

### 6.3 Shoot weights

The new axis set is the six set-mode axes plus `face_sharpness`:

| Axis | Weight (initial) | Source | Rationale |
|---|---|---|---|
| `face_sharpness` | **0.30** | deterministic (face crop) | Missed focus at f/1.4 is the most common and most objective reject. |
| `expression` | **0.30** | gemini | Main human differentiator between frames of one set. |
| `subject_focus` | 0.10 | gemini | Pose/prominence varies a little within a set. |
| `composition` | 0.10 | gemini | Framing varies a little within a set. |
| `camera_engagement` | 0.10 | gemini | Gaze matters for a portrait set. Lower than `family` because looking away is sometimes the intended look. |
| `exposure` | 0.05 | deterministic | Nearly constant within a set. Kept for transparency. |
| `sharpness` | 0.05 | deterministic | Misleading at shallow depth of field (bokeh pulls it down). Kept low on purpose. |
| `face_exposure` | 0.00 | deterministic (face crop) | Returned and hidden by the existing zero-weight filter. Candidate for tuning. |

These are **starting values** to be tuned against the answer key (§12.3).
They live in `core/profiles.py:SHOOT_WEIGHTS`, validated to sum to 1.0, and are
mirrored in `frontend/src/config.js` for on-device re-ranking (the same pattern
as `PROFILE_WEIGHTS`). In Phase A–B full shoot mode does **not** use the v1
profile selector (Q5).

**Blur gate in full shoot mode: off.** The v1 gate uses full-image
`blur_raw`, which is low on good shallow-depth-of-field portraits, so it would
drop exactly the frames we want. Gemini cost for 180 photos is about 20 calls,
which is trivial. Every photo is scored. `blur_raw` and `face_blur_raw` are
still returned for diagnostics. A face-based gate can be revisited after the
evaluation.

**Face detection caveat:** the Haar frontal cascade misses profile and strong
three-quarter faces. In that case `face_detected: false` and the face axes fall
back to full-image values (existing behaviour), which are biased low at f/1.4.
The evaluation measures how often this happens on Wat Arun (§12.4). Eye-region
sharpness (Haar eye cascade inside the face crop) is a Phase C candidate.

### 6.4 Failure semantics

- A group is **atomic**: it is either fully scored or `failed`. Default scores are never assigned (Development Rule 3).
- One failed group does **not** fail the job. Other groups proceed. The failed group shows "Couldn't score this set. Retry". Retry re-uploads that group from IndexedDB as a new one-group chunk.
- This is a deliberate reading of SPECS §4.5 ("partial results are not acceptable"). The rule applies **per Gemini batch**, which is now per group. A failed group is never hidden or given filler scores (Q12).

---

## 7. Boundary detection

### 7.1 Model

Photos are sorted by capture time: `p[0] … p[N-1]`. For each adjacent pair
`(p[i], p[i+1])` we compute three distances and make one decision:
**boundary or not**. Groups are the runs between boundaries. Every boundary
records **why** it fired. This is shown in the UI, so the rule stays
transparent like the scores.

### 7.2 Features (computed on the phone from the compressed canvas)

| Feature | Computation | Range | Captures |
|---|---|---|---|
| `dt` | `t[i+1] − t[i]` in seconds (sub-second when available) | ≥ 0 | Pauses: re-posing, walking to a new spot |
| `dh` | Hamming distance of 64-bit **dHash**: grayscale → 9×8 → compare horizontal neighbours | 0–64 | Structure/framing change (pose, angle, crop) |
| `dc` | `1 − histogram_intersection` of L1-normalised **HSV histogram**, 8 H × 4 S × 4 V = 128 bins, from the 64×64 downscale | 0–1 | Colour/location change (temple wall → river → steps) |

Both visual features cost almost nothing next to the decode already being
done. Storage per photo: 8 bytes of hash plus 128 floats, stored as
`Uint8Array` quantised.

### 7.3 Decision rule (initial thresholds)

A small decision table is used instead of a weighted score, so each boundary
has a one-line reason a person can check:

| # | Condition | Decision | `reason` |
|---|---|---|---|
| 1 | `dt ≥ T_HARD` **and not** (`dh ≤ H_SAME` and `dc ≤ C_SAME`) | boundary | `time_gap` |
| 2 | `dt ≥ T_HARD` and `dh ≤ H_SAME` and `dc ≤ C_SAME` | no boundary | (long pause, same set resumed) |
| 3 | `T_SOFT ≤ dt < T_HARD` and (`dh ≥ H_LOW` **or** `dc ≥ C_LOW`) | boundary | `time+visual` |
| 4 | `dt < T_SOFT` and `dh ≥ H_HIGH` **and** `dc ≥ C_HIGH` | boundary | `visual_change` |
| 5 | otherwise | no boundary | n/a |

In short: a long pause is a new set unless the frames look nearly identical.
A medium pause needs some visual change. A quick follow-up needs a strong
change on both signals.

**Post-pass:**
- `MIN_GROUP`: a group of 1 is merged into whichever neighbour has the smaller combined distance, **unless** `dt ≥ T_HARD` on both sides. A truly isolated frame stays a one-photo group.
- `MAX_GROUP`: a group larger than this is split at its largest internal `dh + 64·dc`, repeating until every piece fits. The split is labelled `reason: "max_size"` so the user can see it was forced.

**Tunable thresholds (initial guesses, to be calibrated in Phase A):**

| Name | Default | Meaning |
|---|---|---|
| `T_SOFT` | 20 s | Below this, frames are presumed same-set (photographer directing between frames). |
| `T_HARD` | 90 s | At or above this, presumed new set (moved location or changed setup). |
| `H_SAME` | 6 | dHash distance for "near-identical framing". |
| `C_SAME` | 0.08 | Histogram distance for "same scene colours". |
| `H_LOW` | 14 | Moderate structural change. |
| `C_LOW` | 0.20 | Moderate colour change. |
| `H_HIGH` | 24 | Strong structural change. |
| `C_HIGH` | 0.35 | Strong colour change. |
| `MIN_GROUP` | 2 | See post-pass. |
| `MAX_GROUP` | 20 | Also the Gemini one-call cap (`GEMINI_GROUP_MAX`). |

The thresholds live in one constants block per implementation
(`core/group.py`, `frontend/src/lib/group.js`), versioned as
`GROUPING_VERSION`. Phase A delivers a histogram of `dt`, `dh`, and `dc` for
true-boundary and non-boundary pairs on Wat Arun, so the defaults are set from
data (§12.2).

**Optional, evaluated rather than shipped by default:** an *adaptive* time
term, where a pair counts as a long pause if `dt > k × median(dt over the
previous 10 pairs)`. This handles shooters whose pace varies. It is only
adopted if it beats fixed thresholds on the answer key.

### 7.4 Missing data

- `time_source == "none"` for a pair: drop the time column. Boundary if `dh ≥ H_LOW and dc ≥ C_LOW`. Reason `visual_only`.
- `file_mtime` times are used, but the results header shows "Capture times unavailable; sets may need adjusting".
- Fingerprint failed (decode failure, original uploaded): the pair is decided on time only.

### 7.5 Output shape

```json
{
  "grouping_version": "1",
  "thresholds": { "T_SOFT": 20, "T_HARD": 90, "H_SAME": 6, "C_SAME": 0.08,
                  "H_LOW": 14, "C_LOW": 0.20, "H_HIGH": 24, "C_HIGH": 0.35,
                  "MIN_GROUP": 2, "MAX_GROUP": 20 },
  "groups": [
    {
      "group_id": "g01",
      "photo_ids": ["DSC01001.JPG", "DSC01002.JPG", "…"],
      "source": "auto",
      "boundary_before": { "reason": "start" }
    },
    {
      "group_id": "g02",
      "photo_ids": ["DSC01013.JPG", "…"],
      "source": "auto",
      "boundary_before": { "reason": "time_gap", "dt_s": 142.3, "dh": 31, "dc": 0.41 }
    }
  ]
}
```

`group_id` values are stable strings assigned at grouping time. Edits create
new IDs (`g02a`, `g02b` on split; `g02+g03` on merge) so in-flight results
can never attach to the wrong group.

### 7.6 Keeping the two implementations in sync

`tests/fixtures/grouping_golden.json` holds a list of per-photo
`{captured_at_ms, dhash, hist}` and the expected groups. Both `core/group.py`
and `frontend/src/lib/group.js` must reproduce it exactly. Feature
*extraction* differs slightly between PIL and canvas resampling. The
evaluation checks that this difference doesn't change boundaries on Wat Arun
(§12.5).

---

## 8. Data shapes

### 8.1 Per-photo result (full shoot mode)

v1's per-photo object plus face axes. One shape across all axes, so the
existing `buildBreakdown()` renders it unchanged:

```json
{
  "photo_id":             "DSC01007.JPG",
  "sharpness":            4.12,
  "exposure":             6.80,
  "blur_raw":             38.2,
  "tenengrad_raw":        610.5,
  "face_detected":        true,
  "face_sharpness":       8.41,
  "face_exposure":        6.95,
  "face_blur_raw":        402.7,
  "subject_1_expression": 8,
  "subject_2_expression": null,
  "expression":           8,
  "camera_engagement":    9,
  "composition":          7,
  "subject_focus":        8,
  "relative_rank":        1,
  "notes":                "eyes are tack-sharp and the half-smile reads natural; best of the set",
  "final_score":          7.958,
  "final_rank":           1,
  "score_breakdown": {
    "face_sharpness":    {"raw": 8.41, "weight": 0.30, "effective_weight": 0.30, "contribution": 2.523, "source": "deterministic (face crop)"},
    "expression":        {"raw": 8,    "weight": 0.30, "effective_weight": 0.30, "contribution": 2.400, "source": "gemini"},
    "subject_focus":     {"raw": 8,    "weight": 0.10, "effective_weight": 0.10, "contribution": 0.800, "source": "gemini"},
    "composition":       {"raw": 7,    "weight": 0.10, "effective_weight": 0.10, "contribution": 0.700, "source": "gemini"},
    "camera_engagement": {"raw": 9,    "weight": 0.10, "effective_weight": 0.10, "contribution": 0.900, "source": "gemini"},
    "exposure":          {"raw": 6.80, "weight": 0.05, "effective_weight": 0.05, "contribution": 0.340, "source": "deterministic"},
    "sharpness":         {"raw": 4.12, "weight": 0.05, "effective_weight": 0.05, "contribution": 0.206, "source": "deterministic"},
    "face_exposure":     {"raw": 6.95, "weight": 0.00, "effective_weight": 0.00, "contribution": 0.000, "source": "deterministic (face crop)"}
  }
}
```

### 8.2 Phone-side session (IndexedDB, database `photorank`, version 1)

Store `sessions` (key `session_id`). At most **one** session is kept:

```json
{
  "session_id": "s_6f1c…",
  "created_at": 1759480000000,
  "status": "preparing | uploading | scoring | done | failed",
  "job_id": "j_9b2e…",
  "photo_count": 180,
  "grouping": { "...": "§7.5 shape" },
  "upload": { "chunks_sent": [0, 1, 2], "groups_uploaded": ["g01", "g02"] },
  "results": { "g01": { "...": "server group result, §9.5" } },
  "overrides": { "g04": "DSC01061.JPG" },
  "stars": ["g01", "g04", "g09"],
  "edits": [ { "op": "split", "group_id": "g07", "before_photo_id": "DSC01110.JPG", "at": 1759480123000 } ]
}
```

Store `photos` (key `[session_id, photo_id]`):

```json
{
  "session_id": "s_6f1c…",
  "photo_id": "DSC01007.JPG",
  "seq": 6,
  "captured_at_ms": 1759479731340,
  "time_source": "exif_subsec",
  "dhash": "c3e1f0f8783c1e0f",
  "hist": "<Uint8Array(128)>",
  "orig": { "size": 10485123, "lastModified": 1759479900000 },
  "blob": "<Blob image/jpeg ~400 KB, EXIF-free>",
  "compressed": true
}
```

**Privacy bounds for IndexedDB (relaxing the v1 "no durable persistence" client rule, as the requirements allow):**
- Only **compressed, EXIF-free** blobs plus the minimal metadata above. No GPS, no other EXIF.
- One session at a time. Starting a new shoot deletes the old one first.
- Cleared on "New shoot" and on successful "Done" (Q7).
- **Auto-purge on app load** if `created_at` is older than `SESSION_TTL` (proposed 24h, matching the Access session). Q7.
- Never sent anywhere except the job upload. Never read by the service worker.
- SPECS §9.7 and CLAUDE.md "Privacy on the client" get updated to describe this exception when Phase B ships.

v1's `sessionStorage` result persistence is unchanged.

### 8.3 Pi-side job state (in memory only)

```python
Job:
  job_id: str            # uuid4 hex, unguessable
  created_at, last_activity: float
  status: "receiving" | "scoring" | "done" | "failed" | "expired"
  expected_groups: int | None    # from create, confirmed at finalize
  finalized: bool
  chunks_seen: set[int]          # idempotency
  groups: dict[group_id, GroupState]

GroupState:
  status: "queued" | "scoring" | "scored" | "failed"
  photo_count: int
  tmp_dir: Path | None           # None once deleted
  result: dict | None            # §9.5 group object (scores only, no pixels)
  error: {code, message} | None
  scored_seq: int                # monotonically increasing; drives ?since=
```

---

## 9. API changes

v1 endpoints (`POST /rank`, `GET /health`) are **unchanged**. New endpoints
live under `/jobs`. They are all on `photorank.job-joseph.com`, so Cloudflare
Access covers them with no code, and the existing service worker already
bypasses this host and all non-GET requests.

### 9.1 `POST /jobs`: create

```json
// request (application/json)
{ "mode": "shoot", "expected_photos": 180, "expected_groups": 19, "client_version": "2.0.0" }

// 201
{
  "job_id": "j_9b2e…",
  "limits": { "max_photos_per_chunk": 20, "max_chunk_bytes": 10485760, "max_group_photos": 20 },
  "poll_interval_s": 3,
  "expires_after_idle_s": 1800
}
```

Errors: 422 (bad mode/counts), 429 (too many active jobs, proposed cap 2,
with `Retry-After`).

### 9.2 `POST /jobs/{job_id}/chunks`: upload whole groups

`multipart/form-data`:

| Field | Notes |
|---|---|
| `files` | Compressed JPEGs. Filename = `photo_id`. |
| `manifest` | JSON string: `{"chunk_index": 3, "groups": [{"group_id": "g07", "photo_ids": ["DSC01101.JPG", "…"]}]}` |

Rules:
- Every file must be listed in exactly one group in the manifest, and the reverse. Otherwise the whole chunk gets a 422 and nothing is kept.
- A group must arrive whole in one chunk. One group is ≤ `MAX_GROUP` (20), so it always fits.
- **Idempotent:** a repeated `chunk_index`, or a `group_id` that is already queued or scored, returns `200 {"duplicate": true}`. The files are discarded immediately. This makes retry-after-timeout safe.
- Response `202 {"accepted_groups": ["g07", "g08"], "queued_groups": 5}`. It returns as soon as files are on disk, typically in a few seconds.

Errors: 404 (unknown/expired job), 409 (job finalized), 413 (chunk too big),
422 (manifest mismatch, >20 photos in a group, bad format), 503 (queue full,
with `Retry-After`; optional backpressure to bound on-disk dwell).

### 9.3 `POST /jobs/{job_id}/finalize`

`{"total_groups": 19}` → `202`. After this the job reaches `done` once every
group is `scored` or `failed`. A mismatch with groups received gives a 422.

### 9.4 `GET /jobs/{job_id}`: poll (small, cheap)

```json
{
  "job_id": "j_9b2e…",
  "status": "scoring",
  "groups_total": 19,
  "groups_received": 19,
  "groups_scored": 7,
  "groups_failed": 0,
  "latest_seq": 7,
  "updated_at": 1759480321
}
```

### 9.5 `GET /jobs/{job_id}/results?since=<seq>`: incremental fetch

```json
{
  "job_id": "j_9b2e…",
  "status": "scoring",
  "mode": "shoot",
  "weights": { "face_sharpness": 0.30, "expression": 0.30, "...": "..." },
  "latest_seq": 9,
  "groups": [
    {
      "group_id": "g08",
      "seq": 8,
      "status": "scored",
      "photo_count": 11,
      "face_detected_count": 10,
      "winner_photo_id": "DSC01118.JPG",
      "ranked": [ /* §8.1 per-photo objects, sorted by final_rank */ ]
    },
    {
      "group_id": "g09",
      "seq": 9,
      "status": "failed",
      "error": { "code": "gemini_failed", "message": "Scoring engine didn't respond for this set." }
    }
  ]
}
```

Results are held only until the job is deleted or expires.

### 9.6 `DELETE /jobs/{job_id}`

Removes tmp files (if any remain) and results immediately. Returns `204`.
Called on "New shoot" and "Cancel".

### 9.7 Re-scoring after a merge

There is no special endpoint. The client creates a **new one-group job** from
IndexedDB blobs and goes through the same create/chunk/finalize/poll path.
This keeps the server surface small.

### 9.8 Server lifecycle and privacy guarantees

- Each chunk is written to `/tmp/photorank_job_{job_id}/{group_id}/`. Each group's directory is deleted in a `try/finally` straight after that group is scored or fails. **Photos live on the Pi only from chunk receipt to group completion** (typically under a minute), not for the whole job.
- **TTL reaper:** every 60s, jobs idle for more than 30 min are deleted (files and results).
- **Startup sweep:** on process start, delete every `/tmp/photorank_job_*` and `/tmp/photorank_*`. `try/finally` doesn't run on power loss or a SIGKILL. The sweep closes that gap. Optional: mount the job root on `tmpfs` (Q11).
- **Rule change to flag:** SPECS §7.3 says nothing persists between requests. Full shoot mode needs job state, which is **scores in memory** across requests for the job's lifetime, plus photos across the gap between chunk upload and group scoring. This is the minimum an async job model requires. It needs explicit sign-off (Q1).
- Logging: job_id, counts, group sizes, timings, status codes. **Never** filenames or manifests.
- **Single uvicorn worker required.** Job state is in-process memory. Multiple workers would route polls to a process that doesn't know the job. Verify the systemd unit (Q10). A Pi restart loses all jobs. The client sees a 404 and re-uploads the groups it has no result for from IndexedDB, with no user action needed.

### 9.9 Expected timing (180 photos, about 19 groups)

| Step | Estimate | Notes |
|---|---|---|
| Phone EXIF + compression + fingerprints | 1.5–3 min | Sequential. Needs measuring on the target phone (§12.6). |
| Upload about 70 MB | 1–2 min on good 4G/Wi-Fi | Starts after grouping. Pi scoring overlaps with it, because each chunk is scored as soon as it lands. |
| Face scoring on Pi | ~0.2–0.4 s/photo | Overlaps with Gemini. |
| Gemini, 19 calls, concurrency 4 | ~5 rounds × 35–45 s ≈ 3–4 min | One call per group. Call latency for groups of up to 20 images is unmeasured. |
| **Total wall clock** | **~5–8 min** | Target to confirm in Q3. |

No single HTTP request comes near 100s.

---

## 10. Frontend

### 10.1 Upload screen

- Segmented control at the top: **Quick pick** (v1, 2–20) | **Full shoot** (2–300). Quick pick is the default and v1's UI is untouched under it.
- Full shoot hides the profile selector (Q5) and shows "For an ordered shoot from one session. We'll split it into sets and pick the best of each."
- If a resumable session exists in IndexedDB, show: "Resume your shoot from earlier (180 photos, 12/19 sets scored)" with **Resume** / **Discard**.

### 10.2 Progress screen

Three real stages, unlike v1's cosmetic progress:
1. **Preparing**: `n / N` compressed (local, exact).
2. **Grouping**: instant. Shows "Found 19 sets".
3. **Uploading and scoring**: two bars from real data. Uploaded groups out of total (client), and scored groups out of total (from `GET /jobs/{id}`).

As soon as the first group is scored, the user can open the results list while
the rest continues.

### 10.3 Results: set list

- Header: "180 photos · 19 sets · 4 starred". **Shortlist** button. A warning chip if capture times were missing.
- One row per set, in shooting order:
  - Winner thumbnail (from the IndexedDB blob), set number, time range ("17:02–17:04"), photo count.
  - Winner's `final_score` and **AI note in quotes** (transparency: reasoning visible without tapping).
  - Small reason chip for the boundary before it ("new set: 2 min gap" / "scene changed"), so a wrong split can be spotted and understood.
  - ★ star toggle.
  - An "Your pick" badge if overridden. A "Re-scoring…" or "Couldn't score: Retry" state where relevant.
- Bulk action: "Star all winners" (Q8).

### 10.4 Results: set detail (tap a set)

- All photos in the set, in rank order. Each expands to the full breakdown using the existing `RankCard` / `AxisBar` / `buildBreakdown()`. The face axes are already supported.
- **"Make this the pick"** on any photo stores `overrides[group_id] = photo_id` (client only). The AI's #1 stays visible as "AI pick" for comparison. Overrides are never sent to the server.
- **Merge with previous set** (button at the top of the set):
  - The two sets were scored in different Gemini calls, so their scores aren't directly comparable (LEARNINGS: relative scoring within a batch).
  - Show a provisional combined ranking right away, labelled "Approximate: re-scoring together…", and send a one-group re-score job (§9.7). Replace the provisional ranking when the job finishes.
  - Offline: keep the provisional ranking, labelled, with "Re-score when online".
- **Split here** (on a photo: "start a new set from this photo", in shooting order):
  - Both halves are subsets of one Gemini call, so the scores stay comparable. Re-rank each half **on the device** (renumber `final_rank`, keep `final_score`; tiebreak by original `relative_rank`) with **no network call**.
  - Overrides or stars on a split or merged group carry to whichever new group contains the overridden photo (Q13).
- Edits are appended to `session.edits` and persisted, so they survive a tab kill.

### 10.5 Shortlist

- A grid of starred sets' picks (the override if present, else the AI winner) with filenames.
- Export options (Q8):
  - **Copy filenames** (`DSC01007, DSC01061, …`) to find the full-resolution originals in the camera roll or Lightroom. Always available.
  - **Share** the picks through the Web Share API. Originals are used while the `File` handles are still in memory. After a resume, only the compressed 1.5 MP copies are available, and the UI says so.

### 10.6 Cloudflare Access expiry

- **Detection:** every `/jobs` call uses `fetch(..., { redirect: "manual" })`. An expired session shows up as one of:
  - `response.type === "opaqueredirect"`, because Access 302s to the login host. With the default `redirect: "follow"`, this surfaces as a CORS **network error** that is indistinguishable from being offline.
  - a 401 or 403
  - a 200 with a `text/html` content type on an endpoint that should return JSON

  All three map to a new `RankError` kind: `"auth"`.
- **UX:** a non-destructive banner or screen: "Your sign-in has expired. Sign in again to keep going. Your photos and progress are saved on this phone." The **Sign in again** button does `window.location.assign("/")`. This is a top-level navigation, so Access shows its OTP page and returns to the app. On load, the app finds the IndexedDB session and resumes polling. If the job expired meanwhile, it re-uploads the groups that have no result yet.
- **Pre-flight:** before starting a full-shoot upload, call `GET /health` with the same detection, so an already-expired session is caught before the user waits through compression and upload.
- v1 `POST /rank` gets the same detection (it's a shared helper). This is a small, contained improvement to v1 error handling. The v1 flow is otherwise unchanged.
- Phase C candidate: warn when the session is close to expiry (Q14).

### 10.7 Service worker

- Already bypasses every non-GET request and the `job-joseph.com` host, so `/jobs/*` is never cached. Add an explicit `/jobs` guard and a comment anyway, as belt and braces.
- The SW never touches IndexedDB.
- **Found in passing:** because the PWA itself is served from `photorank.job-joseph.com`, the host check `url.hostname.endsWith("job-joseph.com")` matches **every** same-origin request. That includes navigations, which would mean the offline-shell branches never run in production. Worth verifying, since resume-after-kill benefits from the shell opening offline (Q15). Not changed by this doc.

---

## 11. Phased build plan

Each phase is independently shippable and has an explicit gate, following the
ROADMAP pattern.

### Phase A: Grouping and shoot scoring engine (CLI + evaluation)

**Goal:** prove the grouping and per-set picks on Wat Arun, from the terminal, before any UI work.

In scope:
- `core/group.py`: EXIF time (PIL; `SubsecTimeOriginal`), dHash, HSV histogram, the §7.3 decision table, post-pass, and boundary reasons.
- `core/profiles.py`: `SHOOT_WEIGHTS` plus validation for the 8-axis set.
- `core/score_vision.py`: `batch_size` parameter and a shoot-mode prompt addendum (and a spread on/off switch for evaluation).
- `core/rank.py --mode shoot --input <folder>`: group, then face and Gemini scoring per group, then rank. Output JSON in the §9.5 shape plus grouping (§7.5).
- `scripts/eval_shoot.py` and the answer-key format (§12).
- `tests/fixtures/grouping_golden.json` and unit tests for the decision table.

Out of scope: API, frontend.

**Gate:** on Wat Arun, the §12 targets are met (or the owner accepts the measured numbers). Thresholds and weights are set from the sweep, and LEARNINGS.md is updated with what the evaluation showed.

*Shippable as:* a working CLI for culling a shoot on the laptop or Pi.

### Phase B: Async jobs API and the full shoot PWA flow (read-only sets)

**Goal:** the full flow on a real phone for 180 photos, with no boundary editing yet.

In scope:
- `api/jobs.py` (router mounted in `api/main.py`): §9.1–9.6, in-memory job store, worker pool, per-group deletion, TTL reaper, startup sweep.
- Frontend: mode toggle, EXIF reader, sequential compression plus fingerprints, `lib/group.js` (passing the golden fixture), IndexedDB session store, chunked upload with retry and idempotency, polling, incremental results.
- Results: set list, set detail with breakdowns, **override pick**, **stars**, shortlist with copy-filenames export.
- Access expiry detection and the sign-in-again flow (§10.6). Resume after a tab kill.
- SPECS, CLAUDE.md, and ROADMAP updated (new mode, API, IndexedDB privacy exception).

Out of scope: merge, split, re-score.

**Gate:**
- 180-photo Wat Arun run end to end on the target phone with no browser crash.
- Total wall-clock time within the Q3 target.
- Filesystem check on the Pi: group directories gone after scoring, nothing left after `DELETE` or TTL.
- Killing the tab during upload and during scoring both resume correctly.
- An expired Access session shows the sign-in message (not "offline"), and the flow resumes after sign-in.
- The service worker is confirmed not to cache `/jobs`.

*Shippable as:* full shoot mode with auto-grouping. Wrong boundaries can't be fixed yet, but override and star already give a usable shortlist.

### Phase C: Boundary correction and refinement

In scope:
- Merge-with-previous (provisional ranking plus re-score job), split-here (on-device re-rank), edit persistence, and carrying overrides and stars over.
- Retry for failed groups from IndexedDB.
- Based on Phase A/B evaluation: eye-region sharpness, adaptive time term, face-based blur gate, session-expiry warning.
- Share-originals export.

**Gate:** a non-technical user fixes a deliberately wrong boundary (one merge, one split) without help. Re-scored merges replace provisional rankings. No regressions in Phase B gate items.

---

## 12. Evaluation against a hand-labelled answer key

### 12.1 Answer key format

`eval/<shoot>/answer_key.json`. It contains filenames only, no image data.
Photos stay in git-ignored `input/` (Q16 on whether the key itself is
committed).

```json
{
  "shoot": "wat_arun_2026",
  "camera": "Sony A7 III, 85mm f/1.4",
  "photo_count": 180,
  "sets": [
    { "set": 1, "first": "DSC01001", "last": "DSC01012", "pick": "DSC01007" },
    { "set": 2, "first": "DSC01013", "last": "DSC01020", "pick": "DSC01016" }
  ]
}
```

Rules for whoever labels it:
- Sets must cover every photo, contiguously, with no overlaps. The harness validates this.
- Exactly **one** pick per set: the frame you would post.
- Optional `"acceptable": ["DSC01008"]` for near-ties, reported as a separate lenient metric only.
- Write down **what makes a new set** before labelling (Q17). For example: same location and pose idea = same set, even if framing changes.

### 12.2 Boundary metrics

Adjacent pairs are treated as N−1 binary decisions (boundary or not):

| Metric | Definition | Initial target |
|---|---|---|
| Boundary precision / recall / F1 | Over the N−1 pairs | F1 ≥ 0.90 |
| **Corrections needed** | False positive boundaries (each needs a merge) + missed boundaries (each needs a split) | **≤ 3 on Wat Arun** |
| Exact-set match | Fraction of true sets reproduced exactly | ≥ 80% |
| ±1 tolerant F1 | A boundary off by one frame counts as a hit | Reported only |

"Corrections needed" is the headline because it measures user effort
directly. Each merge or split is one tap.

**Threshold calibration:**
1. Plot `dt`, `dh`, and `dc` distributions for true-boundary pairs vs non-boundary pairs.
2. Grid-sweep the §7.3 thresholds and plot F1 and corrections.
3. Choose values from the **centre of a stable plateau**, not the single best point, to avoid overfitting one shoot.
4. Run the same sweep with the adaptive time term (§7.3) and keep it only if it wins clearly.

### 12.3 Pick metrics (two levels, to separate scoring from grouping)

**(a) Oracle grouping:** score each **true** set from the answer key.

| Metric | Definition | Initial target |
|---|---|---|
| Top-1 accuracy | Model #1 == key pick | ≥ 50% |
| Top-3 accuracy | Key pick in model top 3 | ≥ 80% |
| MRR | Mean of 1 / (rank of key pick) | Reported |
| Random baseline | Expected top-1 = mean(1/set size) | Reported for context (about 10–15% for sets of 5–15) |

**Ablations** on the same scored data (every score is saved by the harness
locally, so weights can be re-run without new Gemini calls):

| Variant | Purpose |
|---|---|
| face_sharpness only | Is face sharpness alone a strong picker at f/1.4? |
| Gemini axes only (set-mode weights renormalised) | What Gemini adds on its own |
| SHOOT_WEIGHTS (combined) | The design default |
| Combined, prompt spread on vs off (Q6) | Does forced spread help or invent differences? |

Expected result: combined beats either alone. If one alone matches the
combined result, simplify and record the finding in LEARNINGS.md.

**Weight tuning:** a coarse grid over SHOOT_WEIGHTS (0.05 steps, sum = 1)
using oracle top-1, then top-3 as tiebreak. With a single shoot, prefer round
weights near the plateau over the exact optimum. Use leave-one-shoot-out once a
second shoot is labelled.

**Gemini nondeterminism:** run oracle scoring 3 times. Report mean ± range
for top-1 and top-3. A change in weights or prompt only counts if it beats
that range.

**(b) End to end:** predicted groups, then predicted winners.
- For each true set, find the predicted group containing the key pick. It is a **hit** if that group's winner is the key pick.
- Also report the number of true sets that have **no** winner of their own (merged into another set's group). These are the shortlist holes a user would need a split to recover.
- Target: within 10 percentage points of oracle top-1.

### 12.4 Diagnostics to record (feed LEARNINGS.md)

- Face detection rate on Wat Arun (`face_detected` false share) and top-1 restricted to face-detected sets.
- **Resolution ablation:** `face_blur_raw` ranking at 1.5 MP vs 3 MP vs full resolution face crops. Does 1.5 MP downscaling hide the f/1.4 focus misses we rely on? This decides Q18.
- Gemini latency per call vs group size (5, 10, 15, 20 images).
- Full-image `blur_raw` distribution on good frames, to confirm turning the v1 blur gate off was right.

### 12.5 Phone-path parity

Run the PWA in a debug build on the same 180 files. Export
`{photo_id, captured_at_ms, time_source, dhash, hist}` and run the Python
decision table on those features and on PIL-extracted features. Pass if the
boundary sets are identical. If not, the evaluation reports which pairs
differ and by how much.

### 12.6 Device metrics (Phase B gate)

- Peak memory and crash-free completion for 180 × 10 MB on the target Android phone.
- Time per stage (prepare, upload, scoring) and total wall clock.
- Resume tests: kill during compression, upload, and scoring.

### 12.7 Harness

```bash
PYTHONPATH=. ./venv/bin/python scripts/eval_shoot.py \
  --input input/wat_arun --key eval/wat_arun/answer_key.json \
  --stage grouping|oracle|e2e|all \
  --cache output/eval_wat_arun_scores.json    # saved scores so weight sweeps don't re-call Gemini
  --runs 3
```

It writes a Markdown report to `output/` (git-ignored). Scores are cached
locally because this runs on the developer's own machine with their own
photos. The service privacy rules (no caching on the Pi) don't apply to an
offline eval cache, but the cache contains only numbers and filenames.

---

## 13. What stays the same (v1 guard rails)

- `POST /rank`, auto burst/set detection, profiles, profile switcher, `sessionStorage` persistence, and the v1 screens are unchanged. The only shared-code change is the Access-expiry detection in `api.js` (§10.6), which only makes error messages more accurate.
- Gemini is still never asked about sharpness or exposure.
- Every score still carries the full breakdown. Nothing is shown as a bare number.
- No default scores on failure.
- Weights are validated to sum to 1.0 on load.

---

## 14. Open questions

These are deliberately not decided here. Each one names the default this doc
assumed so work isn't blocked.

1. **Privacy rule change on the Pi.** The async job model needs per-job state across requests: scores in memory until fetched or expired (30 min idle), and photos on disk from chunk receipt until their group is scored. Is this acceptable as an amendment to SPECS §7.3 ("no caching, nothing persists between requests")? *Assumed: yes, with per-group deletion, TTL reaper, and startup sweep.*
2. **Max photos per shoot.** *Assumed 300.* Is there a real upper bound (e.g. a 600-photo wedding)?
3. **Time budget.** What end-to-end wall-clock time is acceptable for 180 photos? *Assumed ≤ 8 min*, since v1's < 90s target doesn't scale.
4. **Picker limits on the target phone.** Does the Android photo picker allow 180 selected at once through `<input type=file multiple>`? Some devices cap a single selection (often around 100). *Assumed: additive selection covers it.* Needs a device test.
5. **Profiles in full shoot mode.** Should the user's profile (family/portrait/…) apply, for example blended as `face_sharpness w + (1−w) × profile`? Or is one fixed `SHOOT_WEIGHTS` set enough? *Assumed: fixed SHOOT_WEIGHTS for A–B.*
6. **Forced score spread in the Gemini prompt.** Keep or drop it for within-set scoring? *Decided by the §12.3 ablation.*
7. **IndexedDB retention.** Is 24h auto-purge the right TTL? Should a session be cleared automatically after the user exports the shortlist?
8. **Stars and export.** Should winners be starred by default, or nothing starred until the user acts? Is the shortlist output a filename list (to find originals), shared compressed copies, or shared originals? *Assumed: nothing starred by default, plus a "Star all winners" button. Copy filenames always, share where possible.*
9. **EXIF parser.** Use `exifr` lite (a new dependency, about 10 KB) or a hand-written JPEG APP1 reader (no dependency, JPEG-only)? *Leaning towards `exifr`.*
10. **uvicorn workers.** Does the Pi's systemd unit run a single worker? In-memory job state needs one. Must verify before Phase B.
11. **tmpfs for job files on the Pi.** Mount the job root on `tmpfs` so a power cut leaves nothing on disk? It depends on Pi RAM. 70 MB peak is small.
12. **Partial-job semantics.** Is "failed groups shown with retry, other groups delivered" acceptable under the v1 rule "partial results are not acceptable"? *Assumed: yes. Atomicity is per group (one Gemini batch).*
13. **Override and star after edits.** When a set with an override or star is split or merged, should they follow the photo (assumed) or reset?
14. **Session-expiry warning.** Is it worth adding `GET /session` (reading the `Cf-Access-Jwt-Assertion` `exp` claim for display only) so the app can warn before a long upload? *Assumed: Phase C, only if expiries happen mid-shoot.*
15. **Service worker host check.** Confirm whether the offline shell actually works in production (§10.7). If not, fix it separately from v2.
16. **Committing the answer key.** It contains only filenames and set ranges. Commit it under `eval/`, or keep it git-ignored next to the photos?
17. **What counts as a set?** For example, does a change from full-length to headshot at the same spot start a new set? This decides both labelling and how sensitive the threshold for "strong change" (`H_HIGH`/`C_HIGH`) should be. Needs the owner's definition before labelling.
18. **Upload resolution for full shoot.** If the §12.4 ablation shows 1.5 MP hides focus misses, options are about 3 MP uploads (roughly 2× bytes) or a phone-side face crop (no reliable browser face detector, so this is hard). *Assumed: 1.5 MP until data says otherwise.*
19. **Sony sub-second timestamps.** Does the A7 III write `SubSecTimeOriginal`? Does the transfer path (Imaging Edge / card reader / cloud) keep `DateTimeOriginal` intact? Imaging Edge can also be set to transfer 2 MP copies. *Assumed: full-size originals with DateTimeOriginal kept. Check on the reference files.*
20. **Gemini model lifetime.** Full shoot mode makes about 20 calls per shoot with up to 20 images each. Confirm `gemini-2.0-flash` limits (images per request, RPM) and availability for this usage.
