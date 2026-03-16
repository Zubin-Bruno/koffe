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
        "name": "Grains Argentina",
        "slug": "grains-ar",
        "website_url": "https://www.grains.com.ar",
        "country": "Argentina",
        "scraper_module": "koffe.scrapers.sites.grains_ar",
    },
    {
        "name": "Café Tostado Artesanal",
        "slug": "cafe-tostado",
        "website_url": "https://www.cafetostado.com.ar",
        "country": "Argentina",
        "scraper_module": "koffe.scrapers.sites.sample_roaster",
    },
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
]

SAMPLE_COFFEES = [
    {
        "external_id": "seed-001",
        "name": "Ethiopia Yirgacheffe Natural",
        "url": "https://www.grains.com.ar/products/ethiopia-yirgacheffe",
        "price_cents": 499000,
        "currency": "ARS",
        "weight_grams": 250,
        "is_available": True,
        "origin_country": "Ethiopia",
        "process": "Natural",
        "roast_level": "Light",
        "description": "Notas de arándano, bergamota y chocolate negro. Cosecha 2024.",
        "attributes": {"tasting_notes": ["arándano", "bergamota", "chocolate negro"], "variety": "Heirloom", "altitude_masl": 1900},
    },
    {
        "external_id": "seed-002",
        "name": "Colombia Huila Washed",
        "url": "https://www.grains.com.ar/products/colombia-huila",
        "price_cents": 420000,
        "currency": "ARS",
        "weight_grams": 250,
        "is_available": True,
        "origin_country": "Colombia",
        "process": "Washed",
        "roast_level": "Medium",
        "description": "Taza limpia con notas cítricas y caramelo. Perfecto para filtrado.",
        "attributes": {"tasting_notes": ["naranja", "caramelo", "nuez"], "variety": "Castillo", "altitude_masl": 1750},
    },
    {
        "external_id": "seed-003",
        "name": "Guatemala Antigua Honey",
        "url": "https://www.grains.com.ar/products/guatemala-antigua",
        "price_cents": 390000,
        "currency": "ARS",
        "weight_grams": 250,
        "is_available": True,
        "origin_country": "Guatemala",
        "process": "Honey",
        "roast_level": "Medium",
        "description": "Dulzura pronunciada, cuerpo medio. Excelente para espresso.",
        "attributes": {"tasting_notes": ["durazno", "miel", "almendra"], "variety": "Bourbon"},
    },
    {
        "external_id": "seed-004",
        "name": "Kenya AA Washed",
        "url": "https://www.grains.com.ar/products/kenya-aa",
        "price_cents": 550000,
        "currency": "ARS",
        "weight_grams": 250,
        "is_available": False,
        "origin_country": "Kenya",
        "process": "Washed",
        "roast_level": "Light",
        "description": "Acidez brillante y compleja. Notas de grosella negra y tomate.",
        "attributes": {"tasting_notes": ["grosella negra", "tomate", "hibiscus"], "variety": "SL28"},
    },
    {
        "external_id": "seed-005",
        "name": "Brasil Serra da Mantiqueira Natural",
        "url": "https://www.cafetostado.com.ar/products/brasil-mantiqueira",
        "price_cents": 280000,
        "currency": "ARS",
        "weight_grams": 500,
        "is_available": True,
        "origin_country": "Brazil",
        "process": "Natural",
        "roast_level": "Medium-Dark",
        "description": "Chocolatoso y dulce. Ideal para espresso tradicional.",
        "attributes": {"tasting_notes": ["chocolate con leche", "avellana", "caramelo"], "variety": "Yellow Bourbon"},
    },
]


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
