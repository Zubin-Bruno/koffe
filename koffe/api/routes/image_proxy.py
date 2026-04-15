"""Reverse proxy for roaster images that block hotlinking.

Some roasters (e.g. Cuervo) reject image requests that include a Referer
header from a different domain.  This endpoint fetches the image server-side
(no Referer) and streams it back to the browser with a 24-hour cache header.

Only domains on the ALLOWED_DOMAINS allowlist can be proxied — this prevents
abuse as an open proxy.
"""

from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response

router = APIRouter(prefix="/api", tags=["image-proxy"])

# Domains whose images we're allowed to proxy.  Add more as needed.
ALLOWED_DOMAINS = {"cuervocafe.com", "www.cuervocafe.com"}

MAX_SIZE = 5 * 1024 * 1024  # 5 MB
TIMEOUT = 10.0  # seconds

# 1x1 transparent PNG used as a fallback when the upstream fetch fails.
PLACEHOLDER = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


@router.get("/image-proxy")
async def image_proxy(url: str = Query(..., description="External image URL to proxy")):
    # --- Validate domain ---
    parsed = urlparse(url)
    if parsed.hostname not in ALLOWED_DOMAINS:
        return Response(status_code=403, content=b"Domain not allowed")

    # --- Fetch from upstream ---
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            # Safety: reject unexpectedly large responses
            if len(resp.content) > MAX_SIZE:
                return Response(
                    content=PLACEHOLDER,
                    media_type="image/png",
                    headers={"Cache-Control": "public, max-age=300"},
                )

            content_type = resp.headers.get("content-type", "image/jpeg")

            return Response(
                content=resp.content,
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )

    except Exception:
        return Response(
            content=PLACEHOLDER,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=300"},
        )
