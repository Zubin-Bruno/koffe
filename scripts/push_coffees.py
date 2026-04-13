"""
Scrape Flat N' White locally (residential IP, not blocked) and push
the results to the production server via the /api/admin/push-coffees endpoint.

Usage:
    python scripts/push_coffees.py

Requires in .env (or environment):
    ADMIN_TOKEN   — same value set on Render
    PRODUCTION_URL — optional, defaults to https://www.xn--busca-kaf-j4a.com.ar
"""

import asyncio
import dataclasses
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")
PRODUCTION_URL = os.getenv("PRODUCTION_URL", "https://www.xn--busca-kaf-j4a.com.ar")


async def main():
    if not ADMIN_TOKEN:
        print("ERROR: ADMIN_TOKEN is not set. Add it to your .env file.")
        return

    print("Scraping Flat N' White locally...")
    from koffe.scrapers.sites.flat_and_white import FlatAndWhiteScraper

    scraper = FlatAndWhiteScraper()
    coffees = await scraper._scrape_via_api()
    print(f"Scraped {len(coffees)} coffees locally.")

    if not coffees:
        print("No coffees found — nothing to push. Check for errors above.")
        return

    payload = {
        "roaster_slug": "flat-and-white",
        "coffees": [dataclasses.asdict(c) for c in coffees],
    }

    print(f"Pushing to {PRODUCTION_URL} ...")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{PRODUCTION_URL}/api/admin/push-coffees",
            params={"token": ADMIN_TOKEN},
            json=payload,
        )
        resp.raise_for_status()
        print(f"Push result: {resp.json()}")


asyncio.run(main())
