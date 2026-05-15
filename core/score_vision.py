"""
Gemini semantic scorer.

Scores only the three attributes that require visual understanding:
  expression    Emotional quality, mood, facial engagement
  composition   Framing, rule of thirds, visual balance, background
  subject_focus Prominence and separation of the main subject

Technical axes (sharpness, exposure) are computed deterministically in
score_tech.py and never sent to Gemini. Keeping the two layers separate
prevents Gemini from being asked to differentiate near-identical burst shots
on technical grounds — a task it cannot reliably perform.

Gemini also returns a relative_rank (1 = best) used as a tiebreaker in rank.py
when final_score values are equal.

Usage:
  from core.score_vision import score_photos
  scores = score_photos(paths, photo_ids=ids, profile="family")
"""

import base64
import json
import os
import re
import time
from pathlib import Path

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL  = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
BATCH_SIZE    = 8
AXES_SEMANTIC = ["expression", "composition", "subject_focus"]

_SCORE_SCHEMA = """{
  "photo_id": "<filename>",
  "expression": <1-10>,
  "composition": <1-10>,
  "subject_focus": <1-10>,
  "relative_rank": <integer, 1=best overall>,
  "notes": "<one specific sentence — the single most important thing about this photo>"
}"""

_SYSTEM_PROMPT = """\
You are a professional photo editor scoring photos for a mobile ranking tool.

Score ONLY semantic and aesthetic attributes. Do NOT assess technical sharpness
or exposure — those are measured separately by computer vision tools and are
not your concern here.

CRITICAL: You are ranking these photos AGAINST EACH OTHER, not against an
abstract standard. Your scores MUST spread across the full 1–10 range:
  - At least one photo must score 8 or higher on each axis
  - At least one photo must score below 5 on each axis
  - Do NOT cluster scores in the 5–7 range — clustering defeats the purpose
    of ranking and makes results useless for selecting the best photo
  - Think of the best photo in the set as your 8–10 anchor, and score the
    rest relative to it

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

Include a relative_rank field: rank all photos from best to worst across all
three axes combined (1 = best overall, 2 = second best, etc.). Each photo must
have a unique rank. Break ties by which photo you would choose to keep if you
could only keep one.

For notes: write exactly one sentence identifying the single most important
strength or flaw. Be specific and actionable:
  Good: "subject's eyes are in shadow, flattening the emotional impact"
  Bad:  "expression could be better"

Return ONLY a valid JSON array, one object per photo, no markdown fences,
no commentary, no trailing text. Schema:
""" + _SCORE_SCHEMA


def _load_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise EnvironmentError("GEMINI_API_KEY not set. Add it to your .env file.")
    return key


def _encode_image(path: str | Path) -> tuple[str, str]:
    p    = Path(path)
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png",  ".webp": "image/webp",
        ".heic": "image/heic",
    }.get(p.suffix.lower(), "image/jpeg")
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8"), mime


def _build_batch_prompt(
    image_paths: list[str | Path],
    photo_ids: list[str],
) -> list:
    """
    Build Gemini content parts with interleaved labels and image data.
    photo_ids are used as labels so Gemini returns the original filenames,
    not the compressed-file names.
    """
    parts = []
    for i, (path, photo_id) in enumerate(zip(image_paths, photo_ids)):
        parts.append(f"Photo {i + 1} — photo_id: {photo_id}")
        data, mime = _encode_image(path)
        parts.append({"inline_data": {"mime_type": mime, "data": data}})
    parts.append("\nScore all photos above. Return a JSON array with one object per photo.")
    return parts


def _parse_scores(raw: str, expected_count: int) -> list[dict]:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start   = cleaned.find("[")
    end     = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in response:\n{raw[:500]}")

    parsed   = json.loads(cleaned[start : end + 1])
    required = set(AXES_SEMANTIC) | {"photo_id", "relative_rank", "notes"}
    for item in parsed:
        missing = required - set(item.keys())
        if missing:
            raise ValueError(f"Score object missing fields {missing}: {item}")
        for axis in AXES_SEMANTIC:
            v = item[axis]
            if not isinstance(v, (int, float)) or not (1 <= v <= 10):
                raise ValueError(f"Score out of range for {axis}: {v} in {item}")
        rr = item["relative_rank"]
        if not isinstance(rr, int) or rr < 1 or rr > expected_count:
            raise ValueError(f"relative_rank out of range: {rr} in {item}")

    return parsed


def _score_batch(
    model: genai.GenerativeModel,
    image_paths: list[str | Path],
    photo_ids: list[str],
    retries: int = 2,
) -> list[dict]:
    parts         = _build_batch_prompt(image_paths, photo_ids)
    last_error    = None
    last_response = ""

    for attempt in range(retries + 1):
        try:
            response      = model.generate_content(parts)
            last_response = response.text
            return _parse_scores(response.text, len(image_paths))
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < retries:
                print(f"  [score_vision] parse error on attempt {attempt + 1}, retrying: {e}")
                time.sleep(1)

    raise RuntimeError(
        f"Gemini scoring failed after {retries + 1} attempts: {last_error}\n"
        f"Last response: {last_response[:500]}"
    )


def score_photos(
    image_paths: list[str | Path],
    photo_ids: list[str] | None = None,
    profile: str = "family",
) -> list[dict]:
    """
    Score images via Gemini for semantic attributes only.

    Args:
        image_paths: Paths to compressed image files (output of ingest).
        photo_ids:   Original filenames, parallel to image_paths. When provided,
                     these are used as photo_id labels in the Gemini prompt so
                     the returned IDs match the originals, not the compressed names.
                     Defaults to the filename portion of each image_path.
        profile:     Scoring profile name passed as context in the prompt.

    Returns:
        List of dicts: photo_id, expression, composition, subject_focus,
        relative_rank, notes.

    Raises:
        EnvironmentError: GEMINI_API_KEY not set.
        RuntimeError:     Gemini returned unparseable JSON after retries.
    """
    if not image_paths:
        return []

    resolved_ids = photo_ids if photo_ids is not None else [Path(p).name for p in image_paths]

    genai.configure(api_key=_load_api_key())
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=_SYSTEM_PROMPT + f"\n\nScoring profile context: {profile}",
    )

    all_scores: list[dict] = []
    batches = [
        (image_paths[i : i + BATCH_SIZE], resolved_ids[i : i + BATCH_SIZE])
        for i in range(0, len(image_paths), BATCH_SIZE)
    ]

    for batch_num, (batch_paths, batch_ids) in enumerate(batches, 1):
        print(f"  [score_vision] batch {batch_num}/{len(batches)} ({len(batch_paths)} photos)...")
        all_scores.extend(_score_batch(model, batch_paths, batch_ids))
        if batch_num < len(batches):
            time.sleep(0.5)

    return all_scores


if __name__ == "__main__":
    import sys
    from core.ingest import ingest, cleanup

    if len(sys.argv) < 2:
        print("Usage: python score_vision.py <directory_or_file> [--profile family]")
        sys.exit(1)

    profile = "family"
    if "--profile" in sys.argv:
        profile = sys.argv[sys.argv.index("--profile") + 1]

    photos, tmp = ingest(sys.argv[1])
    try:
        results = score_photos(
            [p["path"] for p in photos],
            photo_ids=[p["photo_id"] for p in photos],
            profile=profile,
        )
        print(json.dumps(results, indent=2))
    finally:
        cleanup(tmp)
