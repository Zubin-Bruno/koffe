"""
Shared roaster seed data — used by both app startup (auto-seed) and scripts/seed_db.py.

The seed_roasters_if_empty() function is idempotent: it only inserts roasters
when the table is completely empty, so it's safe to call on every app restart.
"""

import pathlib
import shutil

from loguru import logger

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee, Roaster, ScrapeRun

IMAGE_EXTENSIONS = {".jpeg", ".jpg", ".png", ".webp"}

SAMPLE_ROASTERS = [
    {
        "name": "Cuervo Café",
        "slug": "cuervo-cafe",
        "website_url": "https://cuervocafe.com",
        "country": "Argentina",
        "scraper_module": "koffe.scrapers.sites.cuervo_cafe",
    },
    {
        "name": "Puerto Blest Tostadores",
        "slug": "puerto-blest",
        "website_url": "https://www.cafepuertoblest.com",
        "country": "Argentina",
        "scraper_module": "koffe.scrapers.sites.puerto_blest",
    },
    {
        "name": "Fuego Tostadores",
        "slug": "fuego-tostadores",
        "website_url": "https://fuegotostadores.com",
        "country": "Argentina",
        "scraper_module": "koffe.scrapers.sites.fuego_tostadores",
    },
    {
        "name": "Mendel Tostadores",
        "slug": "mendel-tostadores",
        "website_url": "https://www.whatsapp.com/catalog/5491137628574/?app_absent=0&utm_source=ig",
        "country": "Argentina",
        "scraper_module": "koffe.scrapers.sites.mendel_tostadores",
    },
]


def seed_roasters_if_empty() -> bool:
    """Insert the known roasters if the roasters table is empty.

    Returns True if roasters were inserted (fresh DB), False otherwise.
    """
    db = SessionLocal()
    try:
        count = db.query(Roaster).count()
        if count > 0:
            logger.info(f"Roasters table already has {count} rows — skipping seed")
            return False

        for r_data in SAMPLE_ROASTERS:
            db.add(Roaster(**r_data))
            logger.info(f"  + Seeded roaster: {r_data['name']}")

        db.commit()
        logger.info(f"Auto-seeded {len(SAMPLE_ROASTERS)} roasters into empty DB")
        return True
    finally:
        db.close()


def copy_bundled_images() -> int:
    """Copy bundled roaster images to data/images/ if they don't already exist.

    Some roasters (like Mendel) have no website to scrape images from, so their
    images are shipped inside the repo under koffe/assets/. This function copies
    them into the data/images/ directory that the app serves via /images.

    Returns the number of files copied.
    """
    # koffe/assets/mendel-images/ lives next to koffe/db/, so go up two levels
    assets_dir = pathlib.Path(__file__).resolve().parent.parent / "assets" / "mendel-images"
    dest_dir = pathlib.Path("data/images")
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not assets_dir.exists():
        logger.warning(f"Bundled images directory not found: {assets_dir}")
        return 0

    copied = 0
    for src_file in assets_dir.iterdir():
        if src_file.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        dest_file = dest_dir / src_file.name
        if dest_file.exists():
            logger.debug(f"Image already exists, skipping: {dest_file}")
            continue
        shutil.copy2(src_file, dest_file)
        logger.info(f"Copied bundled image: {src_file.name} → {dest_file}")
        copied += 1

    if copied:
        logger.info(f"Copied {copied} bundled image(s) to {dest_dir}")
    else:
        logger.info("All bundled images already present — nothing to copy")
    return copied


def apply_curated_intensity() -> int:
    """Apply curated intensity values to roasters that have NULL fields.

    Some roasters (like Fuego) don't list acidity/sweetness/body on their
    websites, so their scrapers embed CURATED_BALANCE dicts with hand-picked
    values. But those values only reach the DB when the scraper runs. This
    function patches existing rows immediately on app startup so deploys take
    effect without waiting for the next 3 AM scrape.

    Returns the number of rows updated.
    """
    from koffe.scrapers.utils import normalize_name

    db = SessionLocal()
    try:
        # Roasters with curated intensity values
        curated_roasters = {
            "fuego-tostadores": "koffe.scrapers.sites.fuego_tostadores.CURATED_BALANCE",
        }

        total_updated = 0

        for slug, import_path in curated_roasters.items():
            roaster = db.query(Roaster).filter(Roaster.slug == slug).first()
            if not roaster:
                logger.debug(f"Roaster '{slug}' not in DB yet — skipping curated intensity")
                continue

            # Dynamically import the CURATED_BALANCE dict
            try:
                module_path, attr_name = import_path.rsplit(".", 1)
                module = __import__(module_path, fromlist=[attr_name])
                curated_balance = getattr(module, attr_name)
            except (ImportError, AttributeError, ValueError) as e:
                logger.warning(f"Could not import {import_path}: {e}")
                continue

            coffees = db.query(Coffee).filter(Coffee.roaster_id == roaster.id).all()
            updated = 0

            for coffee in coffees:
                balance = curated_balance.get(normalize_name(coffee.name))
                if not balance:
                    continue

                # Only fill in fields that are still NULL — never overwrite existing data
                needs_update = (
                    coffee.acidity is None
                    or coffee.sweetness is None
                    or coffee.body is None
                )
                if not needs_update:
                    continue

                if coffee.acidity is None:
                    coffee.acidity = balance[0]
                if coffee.sweetness is None:
                    coffee.sweetness = balance[1]
                if coffee.body is None:
                    coffee.body = balance[2]

                updated += 1
                logger.info(
                    f"Applied curated intensity to '{slug}/{coffee.name}': "
                    f"A={balance[0]} S={balance[1]} B={balance[2]}"
                )

            if updated:
                db.commit()
                logger.info(f"Applied curated intensity to {updated} {slug} coffee(s)")
                total_updated += updated
            else:
                logger.debug(f"All {slug} coffees already have intensity values")

        if total_updated == 0:
            logger.info("No coffees needed intensity patching at startup")

        return total_updated
    finally:
        db.close()


def remove_deprecated_roaster(slug: str) -> None:
    """Delete a roaster and all its related rows from the DB.

    Used to clean up roasters that were removed from the codebase but still
    exist in a persistent database (e.g. Render.com's disk).  Safe to call
    every startup — if the roaster is already gone it does nothing.

    Deletion order matters: coffees and scrape_runs both hold a foreign-key
    reference to roasters.id, so they must be deleted before the roaster row
    itself, otherwise SQLite will raise a constraint error.
    """
    db = SessionLocal()
    try:
        roaster = db.query(Roaster).filter(Roaster.slug == slug).first()
        if roaster is None:
            logger.info(f"Roaster '{slug}' not in DB — nothing to remove")
            return

        coffees_deleted = db.query(Coffee).filter(Coffee.roaster_id == roaster.id).delete()
        runs_deleted = db.query(ScrapeRun).filter(ScrapeRun.roaster_id == roaster.id).delete()
        db.delete(roaster)
        db.commit()
        logger.info(
            f"Removed deprecated roaster '{slug}' "
            f"({coffees_deleted} coffee(s), {runs_deleted} scrape run(s) deleted)"
        )
    finally:
        db.close()
