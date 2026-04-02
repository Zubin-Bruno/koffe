"""
Shared roaster seed data — used by both app startup (auto-seed) and scripts/seed_db.py.

The seed_roasters_if_empty() function is idempotent: it only inserts roasters
when the table is completely empty, so it's safe to call on every app restart.
"""

from loguru import logger

from koffe.db.database import SessionLocal
from koffe.db.models import Roaster

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
