"""
One-off script to normalize existing tasting notes in the database:
- Fix typos (Durano → Durazno, etc.)
- Normalize plurals (Arándanos → Arándano)
- Split compound notes (vainilla miel → two notes)
- Apply Spanish title case
- Remove duplicates

Usage:
    python scripts/fix_tasting_notes.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee
from koffe.scrapers.utils import normalize_tasting_notes


def main():
    with SessionLocal() as session:
        coffees = session.query(Coffee).all()
        updated = 0

        for coffee in coffees:
            if not coffee.attributes:
                continue

            # attributes is stored as a JSON string in SQLite
            attrs = coffee.attributes if isinstance(coffee.attributes, dict) else json.loads(coffee.attributes)
            old_notes = attrs.get("tasting_notes")
            if not old_notes:
                continue

            new_notes = normalize_tasting_notes(old_notes)

            if new_notes != old_notes:
                print(f"  [{coffee.name}]")
                print(f"    before: {old_notes}")
                print(f"    after:  {new_notes}")

                if new_notes:
                    attrs["tasting_notes"] = new_notes
                else:
                    del attrs["tasting_notes"]

                coffee.attributes = attrs
                updated += 1

        session.commit()
        print(f"\nDone! Updated {updated} of {len(coffees)} coffees.")


if __name__ == "__main__":
    main()
