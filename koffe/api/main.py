import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from koffe.api.routes import chat, coffees, feedback, roasters
from koffe.db.database import create_tables
from koffe.db.seed_data import seed_roasters_if_empty

SCHEDULE_HOUR = int(os.getenv("SCRAPE_SCHEDULE_HOUR", "3"))

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    create_tables()
    logger.info("Database tables ready")

    # Auto-seed roasters on first deploy (empty DB)
    freshly_seeded = seed_roasters_if_empty()

    # Schedule daily scrape
    from koffe.scrapers.runner import run_all_scrapers

    scheduler.add_job(run_all_scrapers, "cron", hour=SCHEDULE_HOUR, minute=0)
    scheduler.start()
    logger.info(f"Scheduler started — scraping daily at {SCHEDULE_HOUR:02d}:00")

    # If we just seeded a fresh DB, trigger an immediate scrape so coffees
    # appear within minutes instead of waiting until 3 AM.
    if freshly_seeded:
        scheduler.add_job(run_all_scrapers, "date")
        logger.info("Fresh DB detected — triggered immediate one-shot scrape")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(
    title="Koffe",
    description="Specialty coffee browser — scrapes Argentine roasters and serves a filterable UI",
    version="0.1.0",
    lifespan=lifespan,
    debug=False,
)

# Static files and templates
import pathlib

BASE_DIR = pathlib.Path(__file__).parent.parent
# Non-editable pip install puts __file__ in site-packages where frontend/ doesn't exist.
# Fall back to /app/koffe (the Docker WORKDIR copy) when that happens.
if not (BASE_DIR / "frontend").exists():
    BASE_DIR = pathlib.Path("/app/koffe")

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "frontend" / "static")),
    name="static",
)

IMAGES_DIR = pathlib.Path("data/images")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
try:
    app.mount("/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")
except Exception as e:
    logger.warning(f"Could not mount /images: {e}")
templates = Jinja2Templates(directory=str(BASE_DIR / "frontend" / "templates"))

# Make templates available to routes via app.state
app.state.templates = templates

# Health check for Render
@app.get("/health")
def health():
    return {"status": "ok"}

# Routers
app.include_router(coffees.router)
app.include_router(roasters.router)
app.include_router(chat.router)
app.include_router(feedback.router)
