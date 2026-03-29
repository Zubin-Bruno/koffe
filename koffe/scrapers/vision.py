"""
Vision helper — extracts intensity values from coffee-card images using Pixtral via OpenRouter.

Puerto Blest (and potentially other roasters) embed acidity / body data in
product images rather than in HTML.  This module sends those images to
OpenRouter's Pixtral Vision API and parses the response into numeric values.

Usage:
    from koffe.scrapers.vision import extract_intensities_from_image

    result = await extract_intensities_from_image("https://example.com/card.jpg")
    # result = {"acidity": 3.875, "body": 3.875}
"""

from __future__ import annotations

import base64
import json
import os

import httpx
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

# ---------------------------------------------------------------------------
# OpenRouter API configuration
# ---------------------------------------------------------------------------
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "google/gemini-2.0-flash-001"  # Vision model, accurate for bar charts


def _get_api_key():
    """Get OpenRouter API key from environment."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return api_key


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
Read the bar chart values for the following two attributes:

- Acidez (Acidity)
- Cuerpo (Body)

Each bar is on a scale from 1 to 10.  Estimate the value as precisely as you
can (e.g. 7.75 if the bar is between 7 and 8, closer to 8).

Return ONLY a JSON object with exactly these keys:
{"acidez": <number>, "cuerpo": <number>}

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

    Returns ``{"acidity": ..., "body": ...}`` where each value is a float
    or None.  On ANY failure the dict values are all None.
    """
    empty: dict[str, float | None] = {"acidity": None, "body": None}

    # --- 1. Check for API key early ---
    if not os.getenv("OPENROUTER_API_KEY"):
        logger.warning("[vision] OPENROUTER_API_KEY not set — skipping image analysis")
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

    # --- 3. Call OpenRouter Vision API (Pixtral) ---
    try:
        api_key = _get_api_key()
        async with httpx.AsyncClient(timeout=60) as http:
            response = await http.post(
                OPENROUTER_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/cafecito-project",  # Optional, helps OpenRouter track usage
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{image_b64}",
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": _VISION_PROMPT,
                                },
                            ],
                        }
                    ],
                    "max_tokens": 256,
                },
            )
            response.raise_for_status()
            data = response.json()
            raw_text = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning(f"[vision] OpenRouter API call failed: {exc}")
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
    }

    logger.info(f"[vision] Extracted intensities: {result}")
    return result


# ---------------------------------------------------------------------------
# Fuego Tostadores — 3 bars (CUERPO, ACIDEZ, DULZOR) on a 1–5 scale
# ---------------------------------------------------------------------------

_FUEGO_VISION_PROMPT = """\
This image is a coffee information card ("ficha técnica"). It contains text data
at the top (origin, variety, process, altitude, etc.) — IGNORE all of that.

Focus ONLY on the section at the BOTTOM of the card, labeled "BALANCE:".
It contains three VERTICAL column bars, labeled from left to right:
  - CUERPO (Body)
  - ACIDEZ (Acidity)
  - DULZOR (Sweetness)

The scale runs from 1 (bottom) to 5 (top), marked by dots on the right side
with horizontal gridlines at each whole number.

Read the height of each bar by checking which gridline its top edge aligns with.
Valid values are: 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, or 5.
If a bar's top edge is halfway between two gridlines, use the .5 value (e.g. 3.5).
Pay close attention when bars have similar heights — compare each bar individually
against the gridlines, not against each other.

Return ONLY a JSON object:
{"cuerpo": <number>, "acidez": <number>, "dulzor": <number>}

No explanation, no markdown — just the JSON object.
"""


async def extract_fuego_intensities(
    image_url: str,
) -> dict[str, float | None]:
    """
    Download *image_url*, send it to Pixtral Vision, and return parsed
    intensity values for Fuego Tostadores coffee cards.

    Fuego uses a 1–5 scale, so no scale conversion is needed.

    Returns ``{"acidity": ..., "body": ..., "sweetness": ...}`` where each
    value is a float or None.  On ANY failure the dict values are all None.
    """
    empty: dict[str, float | None] = {"acidity": None, "body": None, "sweetness": None}

    # --- 1. Check for API key early ---
    if not os.getenv("OPENROUTER_API_KEY"):
        logger.warning("[vision] OPENROUTER_API_KEY not set — skipping Fuego image analysis")
        return empty

    # --- 2. Download image ---
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.get(image_url)
            resp.raise_for_status()
            image_bytes = resp.content
    except Exception as exc:
        logger.warning(f"[vision] Failed to download Fuego image {image_url}: {exc}")
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

    # --- 3. Call OpenRouter Vision API (Pixtral) ---
    try:
        api_key = _get_api_key()
        async with httpx.AsyncClient(timeout=60) as http:
            response = await http.post(
                OPENROUTER_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/cafecito-project",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{media_type};base64,{image_b64}",
                                    },
                                },
                                {
                                    "type": "text",
                                    "text": _FUEGO_VISION_PROMPT,
                                },
                            ],
                        }
                    ],
                    "max_tokens": 256,
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            data = response.json()
            raw_text = data["choices"][0]["message"]["content"].strip()
            # --- Diagnostic: log raw model response for debugging ---
            img_name = image_url.split("/")[-1][:60]
            logger.info(f"[vision] Fuego raw response for {img_name}: {raw_text!r}")
    except Exception as exc:
        logger.warning(f"[vision] OpenRouter API call failed for Fuego: {exc}")
        return empty

    # --- 4. Parse JSON response ---
    try:
        cleaned = raw_text
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
        data = json.loads(cleaned.strip())
    except (json.JSONDecodeError, IndexError) as exc:
        logger.warning(f"[vision] Could not parse Fuego response as JSON: {raw_text!r} ({exc})")
        return empty

    # --- 5. Validate and return (already 1–5 scale, no conversion) ---
    def _clamp_1_5(val) -> float | None:
        try:
            v = float(val)
        except (TypeError, ValueError):
            return None
        if v < 1 or v > 5:
            return None
        return round(v * 2) / 2

    result = {
        "acidity": _clamp_1_5(data.get("acidez")),
        "body": _clamp_1_5(data.get("cuerpo")),
        "sweetness": _clamp_1_5(data.get("dulzor")),
    }

    logger.info(f"[vision] Fuego intensities: {result}")
    return result
