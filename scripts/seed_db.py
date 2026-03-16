"""
Populate the DB with sample data for UI development — no scraping needed.

Usage:
    python scripts/seed_db.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from koffe.db.database import SessionLocal, create_tables
from koffe.db.models import Coffee, Roaster

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
]

SAMPLE_COFFEES = []


def seed():
    create_tables()
    db = SessionLocal()

    try:
        # Insert roasters
        roaster_map = {}
        for r_data in SAMPLE_ROASTERS:
            existing = db.query(Roaster).filter_by(slug=r_data["slug"]).first()
            if not existing:
                roaster = Roaster(**r_data)
                db.add(roaster)
                db.flush()
                roaster_map[r_data["slug"]] = roaster.id
                print(f"  + Roaster: {r_data['name']}")
            else:
                roaster_map[r_data["slug"]] = existing.id
                print(f"  ~ Roaster already exists: {r_data['name']}")

        db.commit()

        # Insert coffees — first 3 belong to grains-ar, last 2 to cafe-tostado
        roaster_ids = list(roaster_map.values())
        now = datetime.utcnow()

        for i, c_data in enumerate(SAMPLE_COFFEES):
            roaster_id = roaster_ids[0] if i < 4 else roaster_ids[1]
            existing = db.query(Coffee).filter_by(
                roaster_id=roaster_id, external_id=c_data["external_id"]
            ).first()

            if not existing:
                coffee = Coffee(
                    roaster_id=roaster_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    **c_data,
                )
                db.add(coffee)
                print(f"  + Coffee: {c_data['name']}")
            else:
                print(f"  ~ Coffee already exists: {c_data['name']}")

        db.commit()
        print("\nSeed complete!")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
