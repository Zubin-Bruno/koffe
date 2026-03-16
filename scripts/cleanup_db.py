"""
Remove all roasters and their coffees that are NOT Cuervo Café or Puerto Blest.

Usage:
    python scripts/cleanup_db.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from koffe.db.database import SessionLocal, create_tables
from koffe.db.models import Coffee, Roaster, ScrapeRun

KEEP_SLUGS = ["cuervo-cafe", "puerto-blest"]


def cleanup():
    create_tables()
    db = SessionLocal()

    try:
        to_remove = db.query(Roaster).filter(~Roaster.slug.in_(KEEP_SLUGS)).all()

        if not to_remove:
            print("Nothing to clean up — only real roasters in DB.")
            return

        for r in to_remove:
            n_coffees = db.query(Coffee).filter(Coffee.roaster_id == r.id).delete()
            n_runs = db.query(ScrapeRun).filter(ScrapeRun.roaster_id == r.id).delete()
            db.delete(r)
            print(f"  - Removed roaster '{r.name}' ({n_coffees} coffees, {n_runs} runs)")

        db.commit()
        print("\nCleanup complete!")

    finally:
        db.close()


if __name__ == "__main__":
    cleanup()
