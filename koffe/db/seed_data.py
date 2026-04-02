"""
Shared roaster seed data — used by both app startup (auto-seed) and scripts/seed_db.py.

The seed_roasters_if_empty() function is idempotent: it only inserts roasters
when the table is completely empty, so it's safe to call on every app restart.
"""

import pathlib
import shutil

from loguru import logger

from koffe.db.database import SessionLocal
from koffe.db.models import Roaster

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
        "name": "Flat N' White",
        "slug": "flat-n-white",
        "website_url": "https://flatnwhite.com",
        "country": "Argentina",
        "scraper_module": "koffe.scrapers.sites.flat_n_white",
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
