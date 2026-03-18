"""
One-off script to clean up existing coffee names in the database:
- Remove weight mentions like '250g', '- 250 G', '1kg' from names
- Normalize to title case (first letter of each word capitalized)

Usage:
    python scripts/fix_names.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee
from koffe.scrapers.utils import normalize_name


def main():
    with SessionLocal() as session:
        coffees = session.query(Coffee).all()
        updated = 0

        for coffee in coffees:
            new_name = normalize_name(coffee.name)
            if new_name and new_name != coffee.name:
                print(f"  {coffee.name!r}")
                print(f"    -> {new_name!r}")
                coffee.name = new_name
                updated += 1

        session.commit()
        print(f"\nDone! Updated {updated} of {len(coffees)} coffees.")


if __name__ == "__main__":
    main()
