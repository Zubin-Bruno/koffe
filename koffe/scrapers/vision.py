"""
Vision helper — extracts intensity values from coffee-card images using Claude.

Puerto Blest (and potentially other roasters) embed acidity / body / sweetness
data in product images rather than in HTML.  This module sends those images to
Claude's Vision API and parses the response into numeric values.

Usage:
    from koffe.scrapers.vision import extract_intensities_from_image

    result = await extract_intensities_from_image("https://example.com/card.jpg")
    # result = {"acidity": 3.875, "body": 3.875, "sweetness": 5.0}
"""

from __future__ import annotations

import base64
import json
import os

import httpx
from loguru import logger

# ---------------------------------------------------------------------------
# Lazy client — only created when actually needed (avoids import-time errors
# if the key isn't set and vision is never called).
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        import anthropic
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Scale conversion
# ---------------------------------------------------------------------------

def _scale_10_to_5(value) -> float | None:
    """Convert a 1-10 scale value to 1-5 scale.  Returns None if invalid."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0 or v > 10:
        return None
    return round(v / 2, 3)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_VISION_PROMPT = """\
This image is a coffee tasting card with bar charts showing intensity values.
Read the bar chart values for the following three attributes:

- Acidez (Acidity)
- Cuerpo (Body)
- Dulzura (Sweetness)

Each bar is on a scale from 1 to 10.  Estimate the value as precisely as you
can (e.g. 7.75 if the bar is between 7 and 8, closer to 8).

Return ONLY a JSON object with exactly these keys:
{"acidez": <number>, "cuerpo": <number>, "dulzura": <number>}

No explanation, no markdown — just the JSON object.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def extract_intensities_from_image(
    image_url: str,
) -> dict[str, float | None]:
    """
    Download *image_url*, send it to Claude Vision, and return parsed
    intensity values scaled to 1–5.

    Returns ``{"acidity": ..., "body": ..., "sweetness": ...}`` where each
    value is a float or None.  On ANY failure the dict values are all None.
    """
    empty: dict[str, float | None] = {"acidity": None, "body": None, "sweetness": None}

    # --- 1. Check for API key early ---
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.warning("[vision] ANTHROPIC_API_KEY not set — skipping image analysis")
        return empty

    # --- 2. Download image ---
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content
    except Exception as exc:
        logger.warning(f"[vision] Failed to download image {image_url}: {exc}")
        return empty

    # Determine media type
    content_type = resp.headers.get("content-type", "")
    if "png" in content_type:
        media_type = "image/png"
    elif "webp" in content_type:
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"

    image_b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    # --- 3. Call Claude Vision ---
    try:
        client = _get_client()
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": _VISION_PROMPT,
                        },
                    ],
                }
            ],
        )
        raw_text = message.content[0].text.strip()
    except Exception as exc:
        logger.warning(f"[vision] Claude API call failed: {exc}")
        return empty

    # --- 4. Parse JSON response ---
    try:
        # Strip markdown fences if Claude wrapped them
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
        data = json.loads(cleaned.strip())
    except (json.JSONDecodeError, IndexError) as exc:
        logger.warning(f"[vision] Could not parse response as JSON: {raw_text!r} ({exc})")
        return empty

    # --- 5. Convert 1-10 → 1-5 scale ---
    result = {
        "acidity": _scale_10_to_5(data.get("acidez")),
        "body": _scale_10_to_5(data.get("cuerpo")),
        "sweetness": _scale_10_to_5(data.get("dulzura")),
    }

    logger.info(f"[vision] Extracted intensities: {result}")
    return result
