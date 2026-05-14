# PhotoRank Technical Specification

This document is the implementation contract for every layer of PhotoRank.
It is precise enough to implement any layer independently.

---

## 1. Accepted Image Formats

| Format | MIME type | Notes |
|---|---|---|
| JPEG | `image/jpeg` | `.jpg`, `.jpeg` |
| PNG | `image/png` | |
| WebP | `image/webp` | |
| HEIC | `image/heic` | Common iPhone format |

**Max file size per image:** 10 MB (enforced at the API layer in Phase 2).
**Client-side compression:** Phase 4 will add compression before upload. Until
then, raw files are accepted.
**Deletion policy:** Every image file on the server must be deleted immediately
after scoring completes — success or failure. This is non-negotiable. No temp
files, no caches, no logs of image content.

---

## 2. Scoring Axes

All axes produce scores in the range **1–10** (float, rounded to 2 decimal
places). Higher is always better.

| Axis | Layer | Type | Null possible? |
|---|---|---|---|
| `sharpness` | deterministic | float | No |
| `exposure` | deterministic | float | No |
| `eye_openness` | deterministic | float \| null | Yes — when no face detected |
| `expression` | gemini | int | No |
| `composition` | gemini | int | No |
| `subject_focus` | gemini | int | No |

`eye_openness` is `null` (JSON null / Python None) when MediaPipe finds no
face in the image. The ranker redistributes its profile weight proportionally
across the other five axes. This redistribution must be recorded in
`effective_weight` in the output so the user can see it happened.

---

## 3. Deterministic Scoring Layer

**Module:** `phase1/blur_filter.py`
**Dependencies:** OpenCV, MediaPipe (optional — degrades gracefully)
**Cost:** Zero. Runs entirely local.

### 3.1 Sharpness

Two measures combined to resist false positives:

**Laplacian variance**
```python
gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
lap  = cv2.Laplacian(gray, cv2.CV_64F).var()
```

**Tenengrad** (mean squared Sobel gradient)
```python
gx  = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
gy  = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
ten = np.mean(gx**2 + gy**2)
```

**Normalization** (log scale, calibrated for phone photos):
```
lap_score = 1.0 + 9.0 * log1p(lap) / log1p(400)
ten_score = 1.0 + 9.0 * log1p(ten) / log1p(2500)
sharpness = clamp((lap_score + ten_score) / 2, 1.0, 10.0)
```

Reference calibration:
| Laplacian var | Tenengrad mean | Expected sharpness |
|---|---|---|
| ~20 | ~80 | ~2–3 (blurry) |
| ~100 | ~400 | ~5–6 (acceptable) |
| ~300 | ~1500 | ~7–8 (sharp) |
| ~600+ | ~3000+ | ~9–10 (very sharp) |

`blur_raw` (raw Laplacian variance, unscaled) is also returned and used as the
blur gate. Images where `blur_raw < blur_threshold` are excluded before Gemini
is called. Default threshold: **100**.

### 3.2 Exposure

```python
gray           = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
mean           = np.mean(gray)                         # 0–255
std            = np.std(gray)
highlight_clip = np.sum(gray > 245) / gray.size        # fraction
shadow_clip    = np.sum(gray < 10)  / gray.size        # fraction
```

Scoring:
```
brightness_score = clamp(10.0 - abs(mean - 125) / 125 * 7.0,  1.0, 10.0)
contrast_score   = clamp(1.0 + 9.0 * min(std, 70) / 70,       1.0, 10.0)
clip_penalty     = (highlight_clip + shadow_clip) * 40.0
exposure         = clamp(brightness_score*0.5 + contrast_score*0.5 - clip_penalty, 1.0, 10.0)
```

### 3.3 Eye Openness

**Requires:** MediaPipe Face Mesh. Returns `null` if not installed or no face found.

**Landmark sets** (6-point EAR per eye):
```python
LEFT_EYE  = [33,  160, 158, 133, 153, 144]
RIGHT_EYE = [362, 385, 387, 263, 373, 380]
```

**Eye Aspect Ratio (EAR)**:
```
pts = [(lm[i].x * w, lm[i].y * h) for i in indices]
EAR = (dist(pts[1], pts[5]) + dist(pts[2], pts[4])) / (2 * dist(pts[0], pts[3]))
```

Use `min(left_EAR, right_EAR)` — scores the worse eye. A single blink fails
the shot.

**Normalization** (calibrated: 0.10 = closed, 0.30 = fully open):
```
eye_openness = clamp(1.0 + 9.0 * (EAR - 0.10) / 0.20, 1.0, 10.0)
```

Images are downscaled to max 640px on the longest dimension before MediaPipe
processing to keep performance acceptable on a Raspberry Pi.

### 3.4 Output Format (single image)

```json
{
  "photo_id":     "IMG_4821.jpg",
  "path":         "/tmp/upload/IMG_4821.jpg",
  "sharpness":    7.43,
  "exposure":     6.18,
  "eye_openness": 8.91,
  "blur_raw":     218.44
}
```

`eye_openness` is `null` when no face is detected.

---

## 4. Gemini Semantic Scoring Layer

**Module:** `phase1/scorer.py`
**Model:** `gemini-1.5-flash`
**Auth:** `GEMINI_API_KEY` environment variable
**Batch size:** Up to 8 images per request

### 4.1 What Gemini Scores

Gemini scores **only** the three axes it can reliably judge from a visual
description. It is explicitly told not to assess technical quality:

| Axis | What it measures |
|---|---|
| `expression` | Emotional quality, mood, facial engagement (or emotional impact for non-people shots) |
| `composition` | Framing, rule of thirds, leading lines, visual balance, background cleanliness |
| `subject_focus` | How prominently and clearly the intended subject occupies the frame |

### 4.2 System Prompt Template

```
You are a professional photo editor scoring photos for a mobile ranking tool.

Score ONLY semantic and aesthetic attributes. Do NOT assess technical sharpness
or exposure — those are measured separately by computer vision tools and are
not your concern here.

Score each photo on three axes, 1–10:

  expression    For photos with people: emotional quality, mood, facial
                engagement, authenticity of expression.
                For photos without people: overall emotional impact,
                atmosphere, sense of life or stillness.
                (1 = flat / lifeless, 10 = compelling / resonant)

  composition   Framing, rule of thirds, leading lines, visual balance,
                background cleanliness, horizon alignment.
                (1 = poorly framed or distracting background,
                 10 = expertly composed)

  subject_focus How prominently and unambiguously the intended subject
                occupies the frame; visual separation from background.
                (1 = subject lost in the scene,
                 10 = subject dominant and unmistakable)

For notes: write exactly one sentence identifying the single most important
strength or flaw. Be specific and actionable:
  Good: "subject's eyes are in shadow, flattening the emotional impact"
  Bad:  "expression could be better"

Return ONLY a valid JSON array, one object per photo, no markdown fences,
no commentary, no trailing text. Schema:
{
  "photo_id": "<filename>",
  "expression": <1-10>,
  "composition": <1-10>,
  "subject_focus": <1-10>,
  "notes": "<one specific sentence>"
}

Scoring profile context: <profile_name>
```

### 4.3 Request Construction

Images are sent as base64-encoded inline data. Each batch is a single
`generate_content` call with interleaved text labels and image parts:

```
"Photo 1 — photo_id: IMG_4821.jpg"
<inline_data: image/jpeg, base64...>
"Photo 2 — photo_id: IMG_4822.jpg"
<inline_data: image/jpeg, base64...>
"Score all photos above. Return a JSON array with one object per photo."
```

### 4.4 Response Contract

Gemini must return a JSON array. Each element:

```json
{
  "photo_id":     "IMG_4821.jpg",
  "expression":   8,
  "composition":  6,
  "subject_focus":9,
  "notes":        "warm light catches the subject's left cheek, creating strong depth"
}
```

**Validation rules:**
- All four keys must be present
- `expression`, `composition`, `subject_focus` must be integers (or floats) in [1, 10]
- `notes` must be a non-empty string
- `photo_id` must match a filename in the batch

### 4.5 Error Handling

| Failure | Behaviour |
|---|---|
| JSON parse failure | Strip markdown fences, re-extract array, retry once with a 1s delay |
| After 2 retries | Raise `RuntimeError` with the raw response — do not silently assign scores |
| API error (rate limit, network) | Surface immediately — do not silently assign scores |
| Missing field in one item | Raise `ValueError` — reject the entire batch response |
| Score out of [1, 10] | Raise `ValueError` — reject the entire batch response |

**Never assign default scores on failure.** The user must know when scoring
failed. Partial results (some photos scored, some not) are not acceptable —
fail the batch.

---

## 5. Ranker — Merge and Weight Layer

**Module:** `phase1/ranker.py`

### 5.1 Scoring Profiles

All six axes must be present in every profile. Weights must sum to 1.0
(tolerance ±0.001). Raise `ValueError` on load if violated.

| Profile | sharpness | exposure | eye_openness | expression | composition | subject_focus |
|---|---|---|---|---|---|---|
| `family`  | 0.15 | 0.08 | 0.20 | 0.25 | 0.12 | 0.20 |
| `portrait`| 0.20 | 0.12 | 0.25 | 0.20 | 0.08 | 0.15 |
| `event`   | 0.15 | 0.15 | 0.10 | 0.15 | 0.25 | 0.20 |
| `custom`  | user-supplied | | | | | |

### 5.2 Eye Openness Weight Redistribution

When `eye_openness` is `null` for a photo, its weight is redistributed
proportionally across the remaining five axes before scoring that photo.
This is per-photo — other photos in the same run are unaffected.

```python
remaining = {k: v for k, v in weights.items() if k != "eye_openness"}
total     = sum(remaining.values())
effective = {k: v * (1.0 + eye_w / total) for k, v in remaining.items()}
```

### 5.3 Final Score

```
final_score = sum(score[axis] * effective_weight[axis] for axis in active_axes)
```

Rounded to 3 decimal places.

### 5.4 Output Format (per photo)

```json
{
  "photo_id":     "IMG_4821.jpg",
  "sharpness":    7.43,
  "exposure":     6.18,
  "eye_openness": 8.91,
  "expression":   8,
  "composition":  6,
  "subject_focus":9,
  "notes":        "warm light catches the subject's left cheek, creating strong depth",
  "final_score":  8.012,
  "final_rank":   1,
  "score_breakdown": {
    "sharpness":    {"raw": 7.43, "weight": 0.15, "effective_weight": 0.15, "contribution": 1.114, "source": "deterministic"},
    "exposure":     {"raw": 6.18, "weight": 0.08, "effective_weight": 0.08, "contribution": 0.494, "source": "deterministic"},
    "eye_openness": {"raw": 8.91, "weight": 0.20, "effective_weight": 0.20, "contribution": 1.782, "source": "deterministic"},
    "expression":   {"raw": 8,   "weight": 0.25, "effective_weight": 0.25, "contribution": 2.0,   "source": "gemini"},
    "composition":  {"raw": 6,   "weight": 0.12, "effective_weight": 0.12, "contribution": 0.72,  "source": "gemini"},
    "subject_focus":{"raw": 9,   "weight": 0.20, "effective_weight": 0.20, "contribution": 1.8,   "source": "gemini"}
  }
}
```

When `eye_openness` is null, `score_breakdown.eye_openness` includes:
```json
{
  "raw": null,
  "weight": 0.20,
  "effective_weight": 0,
  "contribution": 0,
  "source": "deterministic",
  "note": "no face detected — weight redistributed"
}
```

### 5.5 Top-Level Output

```json
{
  "profile":        "family",
  "weights":        {"sharpness": 0.15, "...": "..."},
  "blur_threshold": 100.0,
  "total_photos":   24,
  "scored_photos":  21,
  "skipped_blurry": 3,
  "ranked":         [/* array of per-photo objects, sorted by final_rank */]
}
```

---

## 6. FastAPI API Contract (Phase 2)

**Base URL:** `https://<tunnel>.trycloudflare.com` (Cloudflare Tunnel)
**Auth:** Cloudflare Access (JWT on every request, handled by the tunnel)

### 6.1 POST /rank

Rank a batch of photos.

**Request** — `multipart/form-data`:

| Field | Type | Required | Notes |
|---|---|---|---|
| `files` | file[] | Yes | 1–50 images in accepted formats |
| `profile` | string | Yes | `family`, `portrait`, `event`, or `custom` |
| `weights` | string (JSON) | If `custom` | JSON object with all six axes summing to 1.0 |
| `blur_threshold` | float | No | Default 100.0 |

**Response 200** — application/json:

The full ranker output (see Section 5.5). All uploaded files are deleted
before the response is sent, regardless of success or failure.

**Error responses:**

| Status | Condition |
|---|---|
| 400 | Invalid profile name |
| 422 | Weights JSON malformed, missing axes, or doesn't sum to 1.0 |
| 413 | Any single file exceeds 10 MB |
| 415 | Unsupported image format |
| 500 | Gemini API failure (after retries) — includes raw error message |
| 503 | Gemini API rate limit exceeded |

**Cleanup guarantee:** A FastAPI background task deletes all uploaded files
after the response is dispatched. If the handler raises before cleanup, a
try/finally block in the route handler ensures deletion still runs.

### 6.2 GET /health

```json
{"status": "ok", "gemini_key_set": true}
```

Returns 200 if the service is up. `gemini_key_set` reflects whether
`GEMINI_API_KEY` is set — does not validate the key.

---

## 7. Image Handling Rules

These rules apply in every phase and cannot be relaxed:

1. **No persistence.** Images may only exist on disk during the scoring
   pipeline. Delete immediately after use — before the response is sent.
2. **No logging of image content.** Do not log filenames, file sizes, EXIF
   data, or any metadata that could identify a photo or its subject.
3. **No caching.** Do not cache image data, scores, or any derivative in
   a database, file system, or in-memory store between requests.
4. **Temp directory only.** In Phase 2+, uploaded files go to a
   `tempfile.mkdtemp()` directory, not the project tree.
5. **EXIF stripping.** Strip EXIF before sending to Gemini. Phone photos
   contain GPS coordinates; they must never leave the device.

---

## 8. Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Gemini 1.5 Flash API key |

Loaded via `python-dotenv` from `.env` in the project root. Never hardcoded,
never committed. `.env` is in `.gitignore`.
