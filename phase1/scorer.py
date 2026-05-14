"""
Gemini 1.5 Flash semantic scorer.

Scores only the attributes that require visual understanding:
  expression    Emotional quality, mood, facial engagement
  composition   Framing, rule of thirds, visual balance, background
  subject_focus Prominence and separation of the main subject

Technical axes (sharpness, exposure, eye_openness) are computed
deterministically in blur_filter.py and never sent to Gemini.
Keeping the two layers separate prevents Gemini from being asked to
differentiate near-identical burst shots on technical grounds — a task
it cannot reliably perform.

Usage:
  from phase1.scorer import score_photos
  scores = score_photos(["img1.jpg", "img2.jpg"], profile="family")
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

BATCH_SIZE   = 8
AXES_SEMANTIC = ["expression", "composition", "subject_focus"]

_SCORE_SCHEMA = """{
  "photo_id": "<filename>",
  "expression": <1-10>,
  "composition": <1-10>,
  "subject_focus": <1-10>,
  "notes": "<one specific sentence — the single most important thing about this photo>"
}"""

_SYSTEM_PROMPT = """\
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


def _build_batch_prompt(image_paths: list[str | Path]) -> list:
    parts = []
    for i, path in enumerate(image_paths):
        parts.append(f"Photo {i + 1} — photo_id: {Path(path).name}")
        data, mime = _encode_image(path)
        parts.append({"inline_data": {"mime_type": mime, "data": data}})
    parts.append("\nScore all photos above. Return a JSON array with one object per photo.")
    return parts


def _parse_scores(raw: str) -> list[dict]:
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    start   = cleaned.find("[")
    end     = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in response:\n{raw[:500]}")

    parsed   = json.loads(cleaned[start : end + 1])
    required = set(AXES_SEMANTIC) | {"photo_id", "notes"}
    for item in parsed:
        missing = required - set(item.keys())
        if missing:
            raise ValueError(f"Score object missing fields {missing}: {item}")
        for axis in AXES_SEMANTIC:
            v = item[axis]
            if not isinstance(v, (int, float)) or not (1 <= v <= 10):
                raise ValueError(f"Score out of range for {axis}: {v} in {item}")

    return parsed


def _score_batch(
    model: genai.GenerativeModel,
    image_paths: list[str | Path],
    retries: int = 2,
) -> list[dict]:
    parts          = _build_batch_prompt(image_paths)
    last_error     = None
    last_response  = ""

    for attempt in range(retries + 1):
        try:
            response      = model.generate_content(parts)
            last_response = response.text
            return _parse_scores(response.text)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            if attempt < retries:
                print(f"  [scorer] parse error on attempt {attempt + 1}, retrying: {e}")
                time.sleep(1)

    raise RuntimeError(
        f"Gemini scoring failed after {retries + 1} attempts: {last_error}\n"
        f"Last response: {last_response[:500]}"
    )


def score_photos(
    image_paths: list[str | Path],
    profile: str = "family",
) -> list[dict]:
    """
    Score images via Gemini 1.5 Flash for semantic attributes only.

    Args:
        image_paths: Full paths to images (sharp images only — blurry
                     ones should already be excluded by blur_filter).
        profile:     Profile name passed as context in the prompt.

    Returns:
        List of dicts with keys: photo_id, expression, composition,
        subject_focus, notes.

    Raises:
        EnvironmentError: GEMINI_API_KEY not set.
        RuntimeError:     Gemini returned unparseable JSON after retries.
    """
    if not image_paths:
        return []

    genai.configure(api_key=_load_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=_SYSTEM_PROMPT + f"\n\nScoring profile context: {profile}",
    )

    all_scores: list[dict] = []
    batches = [image_paths[i : i + BATCH_SIZE] for i in range(0, len(image_paths), BATCH_SIZE)]

    for batch_num, batch in enumerate(batches, 1):
        print(f"  [scorer] batch {batch_num}/{len(batches)} ({len(batch)} photos)...")
        all_scores.extend(_score_batch(model, batch))
        if batch_num < len(batches):
            time.sleep(0.5)

    return all_scores


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scorer.py <image_or_directory> [--profile family]")
        sys.exit(1)

    from phase1.blur_filter import collect_images

    target  = Path(sys.argv[1])
    profile = "family"
    if "--profile" in sys.argv:
        profile = sys.argv[sys.argv.index("--profile") + 1]

    paths   = collect_images(target) if target.is_dir() else [target]
    results = score_photos(paths, profile=profile)
    print(json.dumps(results, indent=2))
