"""
One-off script to re-encode JSON columns so that Unicode escape sequences
(e.g. \\u00fa) are replaced with actual UTF-8 characters (e.g. ú).

This is needed because Python's json.dumps() defaults to ensure_ascii=True,
which escapes non-ASCII characters.  The new UnicodeJSON column type fixes
future writes, but existing rows still have the old encoding.

Usage:
    python scripts/fix_json_encoding.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm.attributes import flag_modified

from koffe.db.database import SessionLocal
from koffe.db.models import Coffee


def main():
    with SessionLocal() as session:
        coffees = session.query(Coffee).all()
        updated = 0

        for coffee in coffees:
            changed = False

            # flag_modified tells SQLAlchemy "this column changed, please
            # re-save it" — even though the Python dict looks identical,
            # the UnicodeJSON type will re-serialize it with ensure_ascii=False.
            if coffee.attributes is not None:
                flag_modified(coffee, "attributes")
                changed = True
            if coffee.brew_methods is not None:
                flag_modified(coffee, "brew_methods")
                changed = True

            if changed:
                updated += 1

        session.commit()
        print(f"Done! Re-encoded JSON for {updated} of {len(coffees)} coffees.")


if __name__ == "__main__":
    main()
