# V2-PLAN.md — Architecture Review

**Reviewed:** `docs/V2-PLAN.md` (approved for Phase A, 2026-10-03)
**Date:** 2026-11-17
**Scope:** §9 async jobs API, §7 boundary detection, and over-engineering for a
single-user tool. V2-PLAN.md itself is not edited; this file records findings
for the owner to accept or reject.

Severity:

- **must-fix** — the plan is wrong, self-contradictory, or undefined here; building it as written produces a bug or a privacy gap.
- **should-fix** — works as written but has a clear cost or risk with a cheap remedy.
- **consider** — a judgement call; evaluate, then decide.

## Summary

The core design is sound: boundary detection between neighbours, one Gemini call
per set, and per-group deletion are the right ideas and the right size. The
weak spot is §9. The jobs API carries more protocol than one user needs
(chunk indices, finalize, expected counts, job caps, two read endpoints), and
that protocol is where most of the correctness problems live. The privacy claim
"photos live on the Pi typically under a minute" does not hold as specified.

| # | Severity | Area | Finding |
|---|---|---|---|
| J1 | must-fix | §9.8 | No per-group deadline: a hung Gemini call keeps photos on the Pi indefinitely |
| J2 | must-fix | §9.2 | A 20-photo group can exceed the 10 MB chunk cap and become un-uploadable |
| J3 | must-fix | §6.4 / §9.2–9.3 | Retry of a failed group contradicts "409 after finalize" |
| J4 | should-fix | §9.8 | Dwell time is the whole scoring phase, not "under a minute" |
| J5 | should-fix | §9.8 | tmpfs is swap-backed on this Pi; photos can reach the SD card |
| J6 | should-fix | §9.8 | Startup sweep of `/tmp/photorank_*` deletes a running CLI's files; `PrivateTmp` is simpler |
| J7 | should-fix | §9.6 | `DELETE` during scoring is undefined; ingest's second temp dir is not tracked |
| J8 | should-fix | §9.2 | Idempotency by `chunk_index` is the wrong key and has a race |
| J9 | should-fix | §9 | Consider a stateless per-group endpoint instead of jobs; decide after Phase A latency data |
| J10 | consider | §9.1–9.5 | Finalize, expected counts, job cap, 503 backpressure, split poll/results are removable |
| G1 | must-fix (Phase A) | §5.1 / §7.4 | Ordering and `dt` are undefined or wrong when some photos lack EXIF time |
| G2 | must-fix (Phase A) | §7.3 | Post-pass is underspecified: distance, order, and splits that create singletons |
| G3 | should-fix | §7.3 | Rule 4 (`dh` **and** `dc`) conflicts with the Q17 definition of a set |
| G4 | should-fix | §7.3 / §10.4 | A forced `max_size` split cannot be merged back |
| G5 | should-fix | §7.6 | Two implementations are worth keeping, but the fixture must pin thresholds and pair decisions |
| G6 | consider | §7.2 | Hue bins are noise on low-saturation scenes (Wat Arun is largely white) |
| G7 | consider | §7.3 | `MIN_GROUP` merge hides a possibly postable single frame |
| S1 | should-fix | §6.3 | No-face fallback mixes two populations in the heaviest axis |
| S2 | should-fix | §12.3 | The 0.05 weight grid is ~888k candidates fitted to ~19 sets |
| S3 | consider | §6.3 | Log-normalised `face_sharpness` is compressed relative to forced-spread Gemini axes |
| S4 | consider | §6.2 | Forced-spread prompt is impossible to satisfy for a one-photo group |

Applied in Phase A: **G1, G2** (must-fix, in scope). Also built in so the
evaluation can decide them without code changes: G3 (rule-4 combinator is a
threshold-style parameter), G5 (fixture carries thresholds, version, and
per-pair decisions), S2 (default sweep step 0.10), S4 (spread off for a
one-photo group), and J9's input (per-group Gemini latency is recorded).

---

## 1. §9 Async jobs API

### J1 — must-fix — No per-group deadline; a stuck group keeps photos indefinitely

**Problem.** The only time-based cleanup is the TTL reaper: "jobs idle for more
than 30 min are deleted". Nothing bounds a single group. `score_vision.py`
calls `generate_content` with no timeout, and retries parse failures up to
three times. If a call hangs, that group's directory stays in `/tmp` until the
job goes idle. The plan does not say what counts as activity; if the client's
3-second poll counts (the natural reading of `last_activity`), a phone that
keeps polling keeps the photos alive forever. This is the one case where the
design can hold photos without bound.

**Recommendation.**
- Give every group a hard deadline from chunk receipt (suggest 5 min). On expiry: mark the group `failed` (`code: "timeout"`), delete its directory, and discard any late result.
- Pass an HTTP timeout to the Gemini client for shoot calls (suggest 90 s per attempt).
- Define `last_activity` as writes only (create, chunk upload). Polling must not extend a job's life.
- Add both to the Phase B gate: kill the network mid-call and confirm the directory is gone within the deadline.

### J2 — must-fix — A whole group does not always fit in one chunk

**Problem.** §9.2 says a group "always fits" because it is ≤ 20 photos, but the
chunk is also capped at 10 MB (§9.1). §2 estimates 300–500 KB per compressed
photo, so a 20-photo group is 6–10 MB: at or over the cap before multipart
overhead. Worse, §5 keeps v1's fallback of uploading the **original** file when
decoding fails, which is about 10 MB for a single photo. Such a group gets a
413 on every attempt and can never be scored.

**Recommendation.**
- Make one request carry exactly one group and drop chunk packing. Cap by photo count and by a per-file size (suggest 1.5 MB), with the request limit derived from those (20 × 1.5 MB = 30 MB).
- In full shoot mode, never upload an undecodable original. Exclude the photo and say so in the UI ("3 photos couldn't be read").

### J3 — must-fix — Retrying a failed group contradicts the finalize rule

**Problem.** §6.4: a failed group is retried by re-uploading it "as a new
one-group chunk". §9.2: uploading to a finalized job returns 409. Failures are
normally discovered after finalize, so the documented retry path is rejected by
the documented API. §9.3 also 422s when `total_groups` disagrees with groups
received, which a retried group must not trip.

**Recommendation.** Remove finalize (see J10). The client knows the full group
list, so it can tell when it has a result for every group. If finalize is kept,
state that a group in `failed` may be re-uploaded after finalize and replaces
the failed entry.

### J4 — should-fix — Dwell time is the scoring phase, not "under a minute"

**Problem.** §9.8 claims photos live on the Pi "typically under a minute". By
§9.9's own numbers the upload finishes in 1–2 min and Gemini takes 3–4 min at
concurrency 4. Chunks return 202 as soon as files are on disk, so the queue
fills: the last groups wait roughly 3 min, and near the end of the upload most
of the shoot is on the Pi at once. The 503 backpressure that would bound this
is marked optional.

**Recommendation.** Pick one and state it in the plan and in the Q1 amendment:
- Bound it: the client keeps at most `MAX_CONCURRENCY + 2` groups unscored on the server and uploads the next group only when one finishes. Dwell is then about one or two Gemini calls. This costs nothing in total time, because scoring is the bottleneck.
- Or keep eager upload and correct the claim to "up to the full scoring time (about 4 min for 180 photos)".

The first is the better fit for the privacy rules, and it makes the 503 path
unnecessary.

### J5 — should-fix — tmpfs on this Pi can be swapped to the SD card

**Problem.** Q11 asks whether `/tmp` is tmpfs. Checked on the Pi today: it is
(`findmnt /tmp` → tmpfs). But tmpfs pages are swappable, and the Pi currently
has 1.76 GB of its 2 GB swap in use. Under that pressure, job files in `/tmp`
can be written to swap on the SD card, where they outlive deletion and a power
cut. v1 has the same exposure, but only for the length of one request; a job
holds roughly 70 MB for minutes.

**Recommendation.** Bounding dwell (J4) is the main mitigation. Beyond that,
either accept and record the residual risk in SPECS §7, or put the job root on
a dedicated `ramfs` mount, which is never swapped (it has no size limit, so the
server must enforce one). Find out why swap is nearly full before Phase B; it
also affects scoring speed.

### J6 — should-fix — The startup sweep is broader than the service's own files

**Problem.** §9.8 deletes every `/tmp/photorank_*` at process start. The CLI's
`ingest()` also creates `/tmp/photorank_*`. The unit has `Restart=always`, and
every deploy restarts it, so a restart during a CLI or evaluation run (minutes
long for 180 photos) deletes that run's files mid-flight.

**Recommendation.** Add `PrivateTmp=true` to `photorank.service`. systemd then
gives the service its own `/tmp`, removes it whenever the service stops (crash
and SIGKILL included), and hides it from other users. The CLI can no longer
collide, and the sweep becomes a second line of defence over the service's own
root only. This is a one-line change and can ship before Phase B.

Also confirmed today: the unit runs a single uvicorn worker, so Q10 is
satisfied. Add `--workers 1` explicitly so that stays true.

### J7 — should-fix — `DELETE` during scoring, and ingest's second copy

**Problem.** `ingest()` writes a second copy of each photo into its own
`mkdtemp` directory, outside `/tmp/photorank_job_{id}/`. §9.6 says `DELETE`
removes "tmp files (if any remain)", but the job only tracks the group
directory. A `DELETE` while a group is in a Gemini call leaves the ingest copy
until that call returns, and the worker then writes a result into a deleted
job. An in-flight Gemini request cannot be recalled either.

**Recommendation.**
- Skip `ingest()` for job uploads. The files are already about 1.5 MP, EXIF-free JPEGs from the phone; validate that (decodes, dimensions within the limit, no EXIF block) and score in place. This removes the second copy and a lossy re-encode.
- `DELETE` sets a cancelled flag and removes directories; workers check the flag before each stage and drop their result.
- Word the guarantee honestly: "deleted at once, except photos already sent in an in-flight scoring call".

### J8 — should-fix — Idempotency keyed on `chunk_index`

**Problem.** Three issues with §9.2's idempotency:
- `chunk_index` says nothing about content. A client bug or a resume that renumbers chunks makes the server drop real photos and answer `duplicate: true`.
- The plan does not say when the index is recorded. If it is recorded before validation, a chunk that got a 422 is permanently "seen".
- A timed-out request can still be writing when its retry arrives. Both write the same group directory.

**Recommendation.** Key idempotency on `group_id` alone. Register the group
(status `receiving`) under a lock before writing; write to a temporary
directory and rename into place; record success only after the rename. A second
request for a group that is `receiving`, `queued`, `scoring`, or `scored`
returns `duplicate: true`. A `failed` group may be replaced (J3).

### J9 — should-fix — A stateless per-group endpoint may replace the jobs API

**Problem.** Almost every finding above comes from holding state across
requests. The unit of work is one group: at most 20 photos and one Gemini call,
which is the same size as a v1 `/rank` request. v1 runs 20 photos end to end in
about 59 s, inside Cloudflare's 100 s limit.

**Alternative.** `POST /shoot/group`: one group in, the §9.5 group object out,
synchronously, with the phone running four requests at a time.

| | Jobs API (§9) | Per-group request |
|---|---|---|
| Server state | Jobs, groups, results, reaper, sweep | None |
| SPECS §7.3 amendment (Q1) | Needed | Not needed; same guarantee as v1 |
| Photo dwell | Queue time plus scoring | Scoring time only |
| Idempotency, restart, finalize | All must be designed | Retry is "send it again" |
| Phone locked or backgrounded | Scoring continues on the Pi | In-flight groups fail and are resent |
| Request duration | Seconds | One Gemini call; risky if a 20-image call nears 100 s |

The jobs API wins on two points only: the phone can sleep once the upload is
done, and no request depends on Gemini latency. Both are real. Whether they are
worth the rest depends on a number the plan says is unmeasured (§9.9): the
latency of one call at 5, 10, 15, and 20 images on `gemini-3.8-flash`.

**Recommendation.** Treat that measurement as a decision input for Phase B, not
only a diagnostic. If the 95th percentile for the largest group stays under
about 60 s (lowering `MAX_GROUP` to 12–15 is an acceptable way to get there),
build the per-group endpoint and drop §9.1–9.6. Otherwise build the jobs API
with the fixes above. The Phase A harness records per-group latency for this.

### J10 — consider — Protocol surface that one user does not need

Each item is small; together they are most of §9's complexity.

| Item | Why it can go | Replacement |
|---|---|---|
| `finalize`, `expected_groups`, `expected_photos` | The client knows its group list and can tell when it is done | Job status is derived by the client |
| `chunk_index` and chunk packing | See J2 and J8 | One group per request |
| 429 "too many active jobs" (cap 2) | One user. It also blocks §9.7: each merge re-score is a new job that stays active until deleted, so the third merge gets a 429 | Add a group to the existing job |
| 503 queue-full backpressure | Client-side pacing (J4) bounds the queue | None |
| Separate poll (§9.4) and results (§9.5) | Total results are roughly 200 KB | One `GET /jobs/{id}?since=` returning counts and new groups |
| `limits` negotiation, `client_version` | Client and server ship from one repo | Constants |

**Smaller notes.**
- A Pi restart invalidates `seq`. The client must reset its `since` cursor when it creates a replacement job, and should cap automatic re-uploads (suggest two attempts) so a crash loop on the Pi doesn't re-send a shoot forever.
- §9.9 and the "no request near 100 s" claim do not cover retries inside `_score_batch`. This only matters for the per-group design (J9).

---

## 2. §7 Boundary detection

### Is the decision table sound?

Mostly. A table is the right choice over a weighted score: it is explainable,
every error is one merge or split, and the five rows cover the `dt` range with
no gaps or overlaps when all three features are present. The problems are at
the edges: missing data, the post-pass, and one row that conflicts with the
owner's definition of a set.

### G1 — must-fix (Phase A) — Ordering and `dt` with missing or mixed time sources

**Problem.**
- §5.1 sorts by `(captured_at_ms, filename)` but does not say where a photo with `time_source: "none"` goes. It has no sort key.
- A `file_mtime` time is on a different clock from EXIF: after a transfer it is the copy time, hours or days later. Sorted together, such a photo lands at the end of the shoot, away from its set, and the `dt` either side of it is meaningless. §7.4 says these times "are used".
- §7.4 defines "time missing" and "fingerprint missing" separately, but not both missing, and not one of `dh`/`dc` missing.

A handful of edited or re-exported files in a selection is enough to trigger
this, and the result is silently wrong grouping.

**Recommendation (applied in `core/group.py`).**
- If every photo has an EXIF time, sort by `(time, filename)`. Otherwise sort the whole shoot by filename, since DSC order is then the only trustworthy order.
- A pair's `dt` is valid only if both times come from the same clock: both EXIF, or both `file_mtime`. Otherwise the pair is treated as time-missing.
- Fill in the table: time missing uses the `visual_only` rule; fingerprints missing decides on time alone (`dt ≥ T_HARD` → `time_gap`); both missing is no boundary. A missing `dh` or `dc` means the pair has no usable fingerprint.
- The Python reference reads EXIF only. A file's mtime on a laptop or Pi is always a copy time, so it is recorded as `none`. The decision function still accepts `file_mtime` so the phone can use it.

### G2 — must-fix (Phase A) — The post-pass is underspecified

**Problem.**
- "Smaller combined distance" for the `MIN_GROUP` merge is not defined. The `MAX_GROUP` split uses `dh + 64·dc`; the merge should say the same.
- The order of the two passes is not stated. Merging singletons can push a group over `MAX_GROUP`.
- Splitting "at the largest internal distance" can cut one frame off the end, which recreates the singleton the first pass removed.
- A split needs a rule for ties and for pairs with no fingerprint.

Two implementations cannot be kept identical on a rule that is not written
down.

**Recommendation (applied).** Merge first, then split. Distance is
`dh + 64·dc` in both passes; a pair with no fingerprint counts as 0. A split
only considers cut points that leave at least `MIN_GROUP` photos on each side,
and breaks ties toward the middle of the group. A singleton is kept when every
side that has a neighbour is a valid `dt ≥ T_HARD` gap, so a first or last
frame needs only one such gap.

### G3 — should-fix — Rule 4 conflicts with the Q17 definition of a set

**Problem.** Q17 decided that a framing change at the same spot (full-length to
headshot) is a **new** set. Rule 4 fires only when `dh ≥ H_HIGH` **and**
`dc ≥ C_HIGH`. The same spot means similar colours, so `dc` stays low and a
quick re-frame (under 20 s) can never split, whatever the thresholds are. No
threshold sweep can fix a wrong combinator.

**Recommendation.** Make the rule-4 combinator part of the sweep: `and` (as
written) against `or`, and against `dh` alone. Phase A implements it as a
parameter, `FAST_RULE`, defaulting to `and` so the plan's behaviour is
unchanged until the data says otherwise. The sweep should also include values
that disable a signal (`H_LOW = 65`), because consecutive portrait frames of a
moving subject may have a high `dh` inside one set; that would make rule 3's
`or` over-split.

### G4 — should-fix — A forced split cannot be undone

**Problem.** A 30-frame set is split by `MAX_GROUP` into two groups with two
winners, scored in separate Gemini calls. The natural correction is "merge with
previous" (§10.4), but the merged group exceeds `GEMINI_GROUP_MAX`, so the
re-score in §9.7 cannot run.

**Recommendation.** Decide before Phase C. The simplest option: a merge that
would exceed the cap is allowed but keeps the provisional ranking, labelled as
approximate. Also report how many true sets on Wat Arun exceed 20 frames; if
none do, record that and defer.

### G5 — should-fix — Python and JS in sync: worth it, with a tighter fixture

**The cost is small where the plan puts it.** The duplicated part is a pure
function of about 100 lines, and a golden fixture is an effective guard. The
real divergence risk is feature extraction (canvas against OpenCV resampling),
and that exists in any design, because the features must be computed on the
phone from the bitmap it already has.

**Alternatives considered.**

| Option | Verdict |
|---|---|
| Grouping on the Pi: the phone posts features, `core/group.py` returns groups | One implementation, but adds a round trip and a server dependency before upload, and sends capture times off the phone. It does not remove the extraction-parity risk. Not worth it. |
| Run the Python in the browser (Pyodide) | About 10 MB of runtime for 100 lines. No. |
| Two implementations plus a golden fixture (the plan) | Keep. |

**Recommendation.** Keep the plan, and close three gaps in §7.6:
- The fixture should carry `grouping_version` and the full threshold set, and each side should assert its own constants equal them. Thresholds will be re-tuned in Python during calibration; without this the JS constants drift silently. (Applied.)
- The fixture should assert per-pair decisions and reasons, not only final groups, with at least one case per table row, per missing-data path, and per post-pass branch. Two wrong decisions can cancel out in the groups. (Applied.)
- Pin the feature definition so both sides implement the same thing: 64×64 area-averaged downscale, Rec. 601 luma, 9×8 area-averaged, bit order row-major with the most significant bit first, 16 hex characters; histogram bin index `h·16 + s·4 + v`, quantised to bytes. (Documented in `core/group.py`.) §12.5 checks parity once; add two or three tiny committed images with expected features so a later change is caught.

### G6 — consider — Hue is noise where saturation is low

**Problem.** The histogram has 8 hue bins in every saturation band. Hue is
undefined for grey or white pixels, so in the lowest saturation band the pixels
scatter across hue bins from sensor noise. Wat Arun is largely white porcelain
and pale sky, so a large share of each frame may sit in that band, raising `dc`
between frames of the same scene.

**Recommendation.** Look at the Phase A `dc` distribution for non-boundary
pairs. If it is wide, collapse hue in the lowest saturation band into one bin
(128 → 100 bins). Decide before the JS port, because it changes the feature and
the fixture.

### G7 — consider — `MIN_GROUP = 2` can hide a postable frame

Under Q17 a set is "any photo the owner might post … as its own photo". A
one-off frame fits that. Merging it into a neighbour turns it into a
losing frame of another set, which is the "shortlist hole" §12.3 counts.
Keeping it costs one small Gemini call. Include `MIN_GROUP = 1` in the sweep
and choose from "corrections needed".

---

## 3. Scoring and evaluation (Phase A scope)

### S1 — should-fix — The no-face fallback puts two populations in one axis

**Problem.** When the Haar cascade misses a face, `face_sharpness` falls back
to full-image sharpness, which §2 says is low on good f/1.4 frames. Inside one
group, frames with a three-quarter or profile pose therefore lose up to 30% of
their score against frontal frames, for a reason unrelated to focus. The winner
is biased toward frontal poses, and the breakdown shows a "face sharpness"
value that was not measured on a face.

**Recommendation.** The evaluation already reports the miss rate (§12.4). Add:
how often the key pick is a no-face frame, and top-1 with the alternative of
dropping `face_sharpness` for a no-face photo and renormalising its other
weights (`effective_weight` exists in the schema for this). Phase A keeps the
existing fallback and reports the numbers, including `face_detected` on every
photo.

### S2 — should-fix — The weight sweep overfits by construction

**Problem.** A 0.05 grid over eight axes summing to 1 is about 888,000
candidates. Top-1 accuracy on about 19 sets moves in steps of 5 percentage
points, and one Gemini run differs from the next. The best cell of that grid is
noise.

**Recommendation.** Sweep at 0.10 (about 19,000 candidates) over scores
averaged across the runs, and report the plateau: the share of candidates
within one set of the best, and the per-axis weight range among them. The
harness defaults to 0.10; 0.05 stays available.

### S3 — consider — `face_sharpness` is compressed relative to Gemini's axes

The log scale turns a 3× difference in `face_blur_raw` (400 against 1200) into
about 1.3 points, or 0.39 after the 0.30 weight. Forced spread makes Gemini
give 8 against 4 on expression: 1.2 after its weight. The objective signal the
plan calls "most common and most objective reject" can be outvoted by
differences Gemini was instructed to produce. The Q6 ablation covers the prompt
side. Also evaluate a within-group normalisation of `face_blur_raw`; the cache
stores the raw values, so this needs no new Gemini calls.

### S4 — consider — Forced spread cannot be satisfied by one photo

"At least one 8+ and one below 5 per axis" has no valid answer for a one-photo
group, which the post-pass allows (isolated frames). Phase A turns spread off
for a group of one, whatever the switch says. Two-photo groups keep the
switch's setting; note that spread then forces one of the two below 5.

### Note — the §8.1 worked example does not add up

The contributions listed in §8.1 sum to 7.869, not the `final_score` of 7.958
shown. The weights and formula are right; only the example total is wrong.
`tests/test_shoot_scoring.py` uses 7.869.

---

## 4. Over-engineering for a single-user tool

Already covered: the jobs protocol (J9, J10) is the largest item. The rest:

| Item | Severity | Recommendation |
|---|---|---|
| `session.edits` log (§8.2) alongside current groups | consider | Store the current grouping and overwrite it. An edit history is only useful for undo, which is not in scope. |
| "At most one session" plus `session_id` keys on every record | consider | With one session, a fixed key is enough. |
| Adaptive time term (§7.3) | consider | Already optional. Do not build it unless fixed thresholds miss the §12.2 target. |
| Three export paths (§10.5) | consider | Copy-filenames covers the stated need (finding originals). Add share only if asked for. |
| `GET /session` expiry warning (Q14) | consider | Already deferred. The pre-flight `/health` check is enough. |
| Three oracle runs for nondeterminism (§12.3) | keep | Cheap, and without it no prompt or weight change can be judged. |
| Golden fixture, answer key, evaluation harness | keep | These are what make the thresholds and weights defensible. |

---

## 5. What Phase A changes because of this review

| Finding | In Phase A |
|---|---|
| G1 | Applied: ordering fallback, same-clock rule for `dt`, completed missing-data table |
| G2 | Applied: post-pass order, distance, minimum-size-preserving split, tie rule |
| G3 | `FAST_RULE` parameter, default `and` (plan behaviour unchanged) |
| G5 | Fixture carries version, thresholds, per-pair decisions; feature definition pinned |
| S2 | Sweep step defaults to 0.10; plateau reported |
| S4 | Spread off for a one-photo group |
| J9 | Per-group Gemini latency and group size recorded in the evaluation cache and report |
| S1, S3, G6, G7 | Reported by the harness where it has the data; no behaviour change |

All J findings are Phase B and need an owner decision before that phase starts.
J6 (`PrivateTmp=true`) is independent of v2 and can ship now.
