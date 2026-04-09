"""
Scraper runner — orchestrates all active roasters, writes results to DB.

Usage:
    from koffe.scrapers.runner import run_all_scrapers
    await run_all_scrapers()
"""

import importlib
import mimetypes
import pathlib
import re
import urllib.request
from datetime import datetime

from loguru import logger
from playwright.async_api import async_playwright
from sqlalchemy.orm import Session

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee, Roaster, ScrapeRun
from koffe.scrapers.base import BaseScraper, CoffeeData

IMAGES_DIR = pathlib.Path("data/images")


def _download_image(url: str, roaster_slug: str, external_id: str) -> str | None:
    """Download an external image and save it locally. Returns the local URL path."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Derive extension from URL or default to .jpg
    url_path = url.split("?")[0]
    ext = pathlib.Path(url_path).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        ext = ".jpg"

    safe_id = re.sub(r"[^\w-]", "_", external_id)[:80]
    filename = f"{roaster_slug}_{safe_id}{ext}"
    dest = IMAGES_DIR / filename

    if dest.exists():
        return f"/images/{filename}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            filename = f"{roaster_slug}_{safe_id}{ext}"
            dest = IMAGES_DIR / filename
            dest.write_bytes(resp.read())
        return f"/images/{filename}"
    except Exception as exc:
        logger.warning(f"Image download failed for {url}: {exc}")
        return None


async def run_all_scrapers() -> None:
    """Entry point called by APScheduler and the manual scrape script."""
    db = SessionLocal()
    try:
        roasters = db.query(Roaster).filter(Roaster.is_active == True).all()
        logger.info(f"Starting scrape run for {len(roasters)} active roaster(s)")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            for roaster in roasters:
                await _scrape_roaster(db, browser, roaster)
            await browser.close()

        logger.info("All scrapers finished")
    finally:
        db.close()


async def run_recovery_scrapes() -> None:
    """Re-scrape roasters whose coffees are all marked unavailable (corrupted state).

    Called on every server startup (for existing DBs).  If no roasters need
    recovery the function returns immediately without launching Playwright.
    """
    db = SessionLocal()
    try:
        roasters = db.query(Roaster).filter(Roaster.is_active == True).all()
        needs_recovery: list[Roaster] = []

        for roaster in roasters:
            total = db.query(Coffee).filter(Coffee.roaster_id == roaster.id).count()
            available = db.query(Coffee).filter(
                Coffee.roaster_id == roaster.id,
                Coffee.is_available == True,
            ).count()
            if total > 0 and available == 0:
                logger.warning(
                    f"[recovery] {roaster.slug}: {total} coffees in DB but 0 available — will re-scrape"
                )
                needs_recovery.append(roaster)

        if not needs_recovery:
            logger.info("[recovery] All roasters healthy — no recovery needed")
            return

        logger.info(f"[recovery] Launching Playwright for {len(needs_recovery)} roaster(s)")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            for roaster in needs_recovery:
                await _scrape_roaster(db, browser, roaster)
            await browser.close()

        logger.info("[recovery] Recovery scrapes finished")
    finally:
        db.close()


async def _scrape_roaster(db: Session, browser, roaster: Roaster) -> None:
    run = ScrapeRun(roaster_id=roaster.id, started_at=datetime.utcnow())
    db.add(run)
    db.commit()

    logger.info(f"[{roaster.slug}] Starting scrape")

    try:
        scraper: BaseScraper = _load_scraper(roaster.scraper_module)
        coffees_data: list[CoffeeData] = await scraper.scrape(browser)

        _upsert_coffees(db, roaster, coffees_data)

        run.status = "success"
        run.coffees_found = len(coffees_data)
        run.finished_at = datetime.utcnow()
        db.commit()

        logger.success(f"[{roaster.slug}] Done — {len(coffees_data)} coffee(s) found")

    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)
        run.finished_at = datetime.utcnow()
        db.commit()
        logger.error(f"[{roaster.slug}] Scrape failed: {exc}")


def _load_scraper(module_path: str) -> BaseScraper:
    """
    Import a scraper class from its dotted module path.

    The module is expected to expose exactly one BaseScraper subclass,
    or a class named after the module (e.g. module 'scrapers.sites.onibus'
    → class 'OnibusScraper').
    """
    module = importlib.import_module(module_path)

    # Find the first BaseScraper subclass defined in this module
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseScraper)
            and attr is not BaseScraper
        ):
            return attr()

    raise ImportError(f"No BaseScraper subclass found in {module_path}")


def _upsert_coffees(
    db: Session, roaster: Roaster, coffees_data: list[CoffeeData]
) -> None:
    """
    Insert new coffees and update existing ones.
    Marks coffees not seen in this run as unavailable.
    """
    now = datetime.utcnow()
    seen_external_ids: set[str] = set()

    for data in coffees_data:
        seen_external_ids.add(data.external_id)

        # Cache external images locally
        if data.image_url and data.image_url.startswith("http"):
            local_path = _download_image(data.image_url, roaster.slug, data.external_id)
            if local_path:
                data.image_url = local_path

        existing = (
            db.query(Coffee)
            .filter_by(roaster_id=roaster.id, external_id=data.external_id)
            .first()
        )

        if existing:
            # Update all fields
            existing.name = data.name
            existing.url = data.url
            existing.price_cents = data.price_cents
            existing.currency = data.currency
            existing.weight_grams = data.weight_grams
            existing.is_available = data.is_available
            existing.image_url = data.image_url
            existing.description = data.description
            existing.origin_country = data.origin_country
            existing.process = data.process
            existing.roast_level = data.roast_level
            if data.acidity is not None:
                existing.acidity = data.acidity
            if data.sweetness is not None:
                existing.sweetness = data.sweetness
            if data.body is not None:
                existing.body = data.body
            existing.variety = data.variety
            existing.altitude_masl = data.altitude_masl
            existing.brew_methods = data.brew_methods
            existing.attributes = data.attributes
            existing.last_seen_at = now
        else:
            db.add(
                Coffee(
                    roaster_id=roaster.id,
                    external_id=data.external_id,
                    name=data.name,
                    url=data.url,
                    price_cents=data.price_cents,
                    currency=data.currency,
                    weight_grams=data.weight_grams,
                    is_available=data.is_available,
                    image_url=data.image_url,
                    description=data.description,
                    origin_country=data.origin_country,
                    process=data.process,
                    roast_level=data.roast_level,
                    acidity=data.acidity,
                    sweetness=data.sweetness,
                    body=data.body,
                    variety=data.variety,
                    altitude_masl=data.altitude_masl,
                    brew_methods=data.brew_methods,
                    attributes=data.attributes,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )

    # Guard against empty or partial scrape failures nuking a roaster's catalog.
    existing_available_count = db.query(Coffee).filter(
        Coffee.roaster_id == roaster.id,
        Coffee.is_available == True,
    ).count()

    # Case 0: Scraper returned 0 coffees and all existing are already unavailable
    # — this is a corrupted state that recovery should fix on next startup.
    total_coffees = db.query(Coffee).filter(Coffee.roaster_id == roaster.id).count()
    if not seen_external_ids and existing_available_count == 0 and total_coffees > 0:
        logger.error(
            f"[{roaster.slug}] CORRUPTED STATE: scraper returned 0 coffees and all "
            f"{total_coffees} existing coffees are already unavailable. "
            f"Recovery will trigger on next server restart."
        )
        db.commit()
        return

    # Case 1: Scraper returned 0 coffees but some exist → skip mark-unavailable
    if not seen_external_ids and existing_available_count > 0:
        logger.warning(
            f"Scraper returned 0 coffees but {existing_available_count} were previously "
            f"available — skipping mark-unavailable (possible page load failure)"
        )
        db.commit()
        return

    # Case 2: Scraper returned fewer than 50% of existing available coffees
    # (and there were at least 3 previously) → likely a partial failure
    if (
        existing_available_count >= 3
        and len(seen_external_ids) < existing_available_count * 0.5
    ):
        logger.warning(
            f"Scraper returned only {len(seen_external_ids)} coffees but "
            f"{existing_available_count} were previously available "
            f"(< 50%) — skipping mark-unavailable (possible partial failure)"
        )
        db.commit()
        return

    # Mark coffees not seen in this run as unavailable
    db.query(Coffee).filter(
        Coffee.roaster_id == roaster.id,
        Coffee.external_id.notin_(seen_external_ids),
        Coffee.is_available == True,
    ).update({"is_available": False}, synchronize_session=False)

    db.commit()
