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
            image_bytes = resp.read()

        # Detect actual format from magic bytes — URL extensions and
        # Content-Type headers sometimes lie (e.g. a .jpg URL serving PNG).
        if image_bytes[:8].startswith(b"\x89PNG"):
            ext = ".png"
        elif image_bytes[:3] == b"\xff\xd8\xff":
            ext = ".jpg"
        elif len(image_bytes) >= 12 and image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
            ext = ".webp"
        elif image_bytes[:4] == b"GIF8":
            ext = ".gif"
        # else: keep the extension we derived from the URL above

        filename = f"{roaster_slug}_{safe_id}{ext}"
        dest = IMAGES_DIR / filename
        dest.write_bytes(image_bytes)
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


def _is_garbage(data: CoffeeData, roaster: Roaster) -> bool:
    """Reject a CoffeeData entry that looks like a failed scrape.

    Two heuristics:
    1. The name matches the roaster's domain (e.g. "Flatnwhite.Com") — means
       the scraper grabbed the site header instead of the product title.
    2. ALL substantive fields are None/empty — no price, image, description,
       origin, process, or roast level.  A real product always has at least one.
    """
    # Check if name looks like a domain (strip protocol/www, compare case-insensitive)
    if data.name:
        name_lower = data.name.lower().strip()
        slug_domain = roaster.slug.replace("-", "")  # "flat-n-white" → "flatnwhite"
        # Also check the roaster's URL domain if available
        domain_variants = {slug_domain}
        if roaster.website_url:
            from urllib.parse import urlparse
            parsed = urlparse(roaster.website_url)
            domain = parsed.hostname or ""
            domain_variants.add(domain.replace("www.", "").lower())
            domain_variants.add(domain.replace("www.", "").split(".")[0].lower())
        name_clean = re.sub(r"[.\-_\s]", "", name_lower)
        for variant in domain_variants:
            variant_clean = re.sub(r"[.\-_\s]", "", variant)
            if variant_clean and name_clean == variant_clean:
                logger.warning(
                    f"[{roaster.slug}] Rejecting garbage entry '{data.name}' "
                    f"(external_id={data.external_id}) — name matches domain"
                )
                return True

    # Check if ALL substantive fields are empty
    substantive_fields = [
        data.price_cents, data.image_url, data.description,
        data.origin_country, data.process, data.roast_level,
    ]
    if all(f is None for f in substantive_fields):
        logger.warning(
            f"[{roaster.slug}] Rejecting garbage entry '{data.name}' "
            f"(external_id={data.external_id}) — all substantive fields are null"
        )
        return True

    return False


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
        if _is_garbage(data, roaster):
            continue

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
            # Always update fields that are always present in a valid scrape
            existing.name = data.name
            existing.url = data.url
            existing.currency = data.currency
            existing.is_available = data.is_available
            existing.last_seen_at = now

            # Nullable fields: only overwrite when the new value is not None.
            # This prevents a partial scrape failure from erasing good data.
            if data.price_cents is not None:
                existing.price_cents = data.price_cents
            if data.weight_grams is not None:
                existing.weight_grams = data.weight_grams
            if data.image_url is not None:
                existing.image_url = data.image_url
            if data.description is not None:
                existing.description = data.description
            if data.origin_country is not None:
                existing.origin_country = data.origin_country
            if data.process is not None:
                existing.process = data.process
            if data.roast_level is not None:
                existing.roast_level = data.roast_level
            if data.acidity is not None:
                existing.acidity = data.acidity
            if data.sweetness is not None:
                existing.sweetness = data.sweetness
            if data.body is not None:
                existing.body = data.body
            if data.variety is not None:
                existing.variety = data.variety
            if data.altitude_masl is not None:
                existing.altitude_masl = data.altitude_masl
            if data.brew_methods is not None:
                existing.brew_methods = data.brew_methods
            if data.attributes is not None:
                existing.attributes = data.attributes
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
