# -*- coding: utf-8 -*-
"""
One-time script: manually set tasting notes for Altura 1 - House Blend
from Puerto Blest. These notes live in the dedicated `tasting_notes` column
and are never overwritten by the scraper.
"""

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee, Roaster

db = SessionLocal()

roaster = db.query(Roaster).filter_by(slug="puerto-blest").first()
if not roaster:
    print("ERROR: puerto-blest roaster not found")
    db.close()
    raise SystemExit(1)

coffee = (
    db.query(Coffee)
    .filter_by(roaster_id=roaster.id, external_id="altura-1-house-blend")
    .first()
)
if not coffee:
    print("ERROR: Altura 1 coffee not found")
    db.close()
    raise SystemExit(1)

coffee.tasting_notes = ["Durazno", "Caramelo", "Pecán"]
db.commit()
print(f"Done! tasting_notes = {coffee.tasting_notes}")
db.close()
