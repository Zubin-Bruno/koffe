import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from loguru import logger

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/scrape")
async def trigger_scrape(token: str, background_tasks: BackgroundTasks):
    """
    Manually trigger a scrape run via HTTP.

    Query parameter:
    - token: Must match ADMIN_TOKEN environment variable

    Returns 403 Forbidden if token doesn't match.
    If token is correct, starts the scrape in the background and returns immediately.
    """
    admin_token = os.getenv("ADMIN_TOKEN")

    # If ADMIN_TOKEN is not set, reject the request
    if not admin_token:
        logger.warning("Scrape endpoint hit but ADMIN_TOKEN not configured")
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN not configured")

    # If token doesn't match, reject the request
    if token != admin_token:
        logger.warning(f"Scrape endpoint hit with incorrect token")
        raise HTTPException(status_code=403, detail="Invalid token")

    # Token is valid — add the scraper to background tasks
    from koffe.scrapers.runner import run_all_scrapers

    background_tasks.add_task(run_all_scrapers)
    logger.info("Manual scrape triggered via admin endpoint")

    return {"status": "started"}
