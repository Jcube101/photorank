"""
Gemini 1.5 Flash vision scorer.

Sends batches of images to Gemini and returns structured per-photo scores.
Each photo gets: sharpness, expression, composition, exposure, subject_focus (1-10),
a one-sentence notes field, and a rank within the batch.

Usage:
  from phase1.scorer import score_photos
  results = score_photos(["img1.jpg", "img2.jpg"], profile="family")
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

BATCH_SIZE = 8
AXES = ["sharpness", "expression", "composition", "exposure", "subject_focus"]

SCORE_SCHEMA = """{
  "photo_id": "<filename>",
  "sharpness": <1-10>,
  "expression": <1-10>,
  "composition": <1-10>,
  "exposure": <1-10>,
  "subject_focus": <1-10>,
  "notes": "<one specific sentence — the single most important thing about this photo>",
  "rank": <rank within this batch, 1 = best>
}"""

SYSTEM_PROMPT = """\
You are a professional photo editor scoring photos for a mobile ranking tool.
Score each photo on five axes, each 1–10:
  - sharpness: technical focus and detail clarity
  - expression: emotional quality, mood, facial expressions if present
  - composition: framing, rule of thirds, visual balance, background
  - exposure: brightness, contrast, highlight/shadow recovery
  - subject_focus: how well the main subject is isolated and prominent

For notes: write exactly one specific sentence identifying the single most
important thing — a strength or a flaw — about this photo. Be specific and
actionable, not generic.

Return ONLY a valid JSON array, one object per photo, with no markdown fences,
no commentary, no trailing text. Follow this schema exactly:
""" + SCORE_SCHEMA


def _load_api_key() -> str:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise EnvironmentError(
            "GEMINI_API_KEY not set. Add it to your .env file."
        )
    return key


def _encode_image(path: str | Path) -> tuple[str, str]:
    """Return (base64_data, mime_type) for a local image file."""
    p = Path(path)
    ext = p.suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".heic": "image/heic",
    }
    mime = mime_map.get(ext, "image/jpeg")
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8"), mime


def _build_batch_prompt(image_paths: list[str | Path]) -> list:
    """Build Gemini content parts: interleaved labels and image data."""
    parts = []
    for i, path in enumerate(image_paths):
        photo_id = Path(path).name
        parts.append(f"Photo {i + 1} — photo_id: {photo_id}")
        data, mime = _encode_image(path)
        parts.append({"inline_data": {"mime_type": mime, "data": data}})
    parts.append(
        "\nScore all photos above. Return a JSON array with one object per photo."
    )
    return parts


def _parse_scores(raw: str, expected_ids: list[str]) -> list[dict]:
    """Extract JSON array from Gemini response, validate fields."""
    # Strip markdown fences if present
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()

    # Find the outermost JSON array
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in response:\n{raw[:500]}")

    parsed = json.loads(cleaned[start : end + 1])

    required = set(AXES) | {"photo_id", "notes", "rank"}
    for item in parsed:
        missing = required - set(item.keys())
        if missing:
            raise ValueError(f"Score object missing fields {missing}: {item}")
        for axis in AXES:
            v = item[axis]
            if not isinstance(v, (int, float)) or not (1 <= v <= 10):
                raise ValueError(f"Score out of range for {axis}: {v} in {item}")

    return parsed


def _score_batch(
    model: genai.GenerativeModel,
    image_paths: list[str | Path],
    retries: int = 2,
) -> list[dict]:
    """Score one batch, retry on parse failure with a stricter prompt."""
    expected_ids = [Path(p).name for p in image_paths]
    parts = _build_batch_prompt(image_paths)

    for attempt in range(retries + 1):
        try:
            response = model.generate_content(parts)
            return _parse_scores(response.text, expected_ids)
        except (ValueError, json.JSONDecodeError) as e:
            if attempt == retries:
                raise RuntimeError(
                    f"Gemini scoring failed after {retries + 1} attempts: {e}\n"
                    f"Last response: {response.text[:500]}"
                )
            print(f"  [scorer] parse error on attempt {attempt + 1}, retrying: {e}")
            time.sleep(1)


def score_photos(
    image_paths: list[str | Path],
    profile: str = "family",
) -> list[dict]:
    """
    Score a list of images via Gemini 1.5 Flash.

    Args:
        image_paths: Paths to image files.
        profile: Scoring profile name (for context in prompt).

    Returns:
        List of score dicts, one per image, in input order.
    """
    if not image_paths:
        return []

    genai.configure(api_key=_load_api_key())
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        system_instruction=SYSTEM_PROMPT + f"\n\nScoring profile: {profile}",
    )

    all_scores: list[dict] = []
    batches = [
        image_paths[i : i + BATCH_SIZE]
        for i in range(0, len(image_paths), BATCH_SIZE)
    ]

    for batch_num, batch in enumerate(batches, 1):
        print(
            f"  [scorer] batch {batch_num}/{len(batches)}"
            f" ({len(batch)} photos)..."
        )
        scores = _score_batch(model, batch)
        all_scores.extend(scores)
        if batch_num < len(batches):
            time.sleep(0.5)  # stay well under rate limits

    return all_scores


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python scorer.py <image_or_directory> [--profile family]")
        sys.exit(1)

    from phase1.blur_filter import collect_images

    target = Path(sys.argv[1])
    profile = "family"
    if "--profile" in sys.argv:
        idx = sys.argv.index("--profile")
        profile = sys.argv[idx + 1]

    paths = collect_images(target) if target.is_dir() else [target]
    results = score_photos(paths, profile=profile)
    print(json.dumps(results, indent=2))
