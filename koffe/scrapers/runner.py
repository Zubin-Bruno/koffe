"""
Scraper runner — orchestrates all active roasters, writes results to DB.

Usage:
    from koffe.scrapers.runner import run_all_scrapers
    await run_all_scrapers()
"""

import importlib
from datetime import datetime

from loguru import logger
from playwright.async_api import async_playwright
from sqlalchemy.orm import Session

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee, Roaster, ScrapeRun
from koffe.scrapers.base import BaseScraper, CoffeeData


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
            existing.acidity = data.acidity
            existing.sweetness = data.sweetness
            existing.body = data.body
            existing.variety = data.variety
            existing.altitude_masl = data.altitude_masl
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
                    attributes=data.attributes,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )

    # Mark coffees not seen in this run as unavailable
    db.query(Coffee).filter(
        Coffee.roaster_id == roaster.id,
        Coffee.external_id.notin_(seen_external_ids),
        Coffee.is_available == True,
    ).update({"is_available": False}, synchronize_session=False)

    db.commit()
