"""
Manually update acidity, body, and sweetness for coffees where
those values are only shown in images on the roaster's website.

Usage:
    python scripts/update_intensities.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee

# Each entry: the coffee name (or part of it), and the intensity levels.
# The search is case-insensitive and looks for coffees whose name CONTAINS
# the search string, so you don't need to type the exact full name.
INTENSITY_DATA = [
    {"search": "betulia",           "acidity": 3.0,  "body": 3.0,  "sweetness": 4.0},
    {"search": "blend c",           "acidity": 4.0,  "body": 4.5,  "sweetness": 4.0},
    {"search": "bolivia regional",  "acidity": 2.0,  "body": 3.5,  "sweetness": 3.5},
    {"search": "brasil",            "acidity": 3.0,  "body": 4.0,  "sweetness": 3.5},
    {"search": "fuego negro",       "acidity": 3.0,  "body": 3.0,  "sweetness": 3.0},
    {"search": "verano",            "acidity": 4.0,  "body": 4.0,  "sweetness": 3.0},
    {"search": "las margaritas",    "acidity": 4.0,  "body": 3.0,  "sweetness": 3.0},
    {"search": "volturno",          "acidity": 3.5,  "body": 3.5,  "sweetness": 2.0},
    {"search": "castillo lavado",   "acidity": 4.25, "body": 4.0,  "sweetness": 4.0},
    {"search": "castillo natural",  "acidity": 4.5,  "body": 4.25, "sweetness": 4.25},
]


def main():
    with SessionLocal() as session:
        for entry in INTENSITY_DATA:
            search_term = entry["search"].lower()

            # Find all coffees whose name contains the search term (case-insensitive).
            matches = (
                session.query(Coffee)
                .filter(Coffee.name.ilike(f"%{search_term}%"))
                .all()
            )

            if not matches:
                print(f"  NOT FOUND: '{entry['search']}'")
                continue

            for coffee in matches:
                coffee.acidity = entry["acidity"]
                coffee.body = entry["body"]
                coffee.sweetness = entry["sweetness"]
                print(
                    f"  UPDATED: {coffee.name!r} "
                    f"(acidity={coffee.acidity}, body={coffee.body}, sweetness={coffee.sweetness})"
                )

        session.commit()
        print("\nDone! All changes saved to the database.")


if __name__ == "__main__":
    main()
