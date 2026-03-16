"""
Run all active scrapers immediately from the terminal.

Usage:
    python scripts/scrape_now.py
"""

import asyncio
import sys
from pathlib import Path

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from koffe.db.database import create_tables
from koffe.scrapers.runner import run_all_scrapers

if __name__ == "__main__":
    create_tables()
    asyncio.run(run_all_scrapers())
