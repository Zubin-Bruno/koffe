import os
from fastapi import APIRouter, HTTPException, BackgroundTasks
from loguru import logger

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Track if a scrape is currently running
is_scraping = False


async def scrape_with_status():
    """Wrapper that tracks when scraping starts and finishes."""
    global is_scraping
    is_scraping = True
    logger.info("Scrape started")

    try:
        from koffe.scrapers.runner import run_all_scrapers
        await run_all_scrapers()
    finally:
        is_scraping = False
        logger.info("Scrape finished")


@router.get("/scrape/status")
@router.post("/scrape/status")
async def scrape_status():
    """
    Check if a scrape is currently running.

    Returns:
    - {"is_scraping": true} if scrape is in progress
    - {"is_scraping": false} if idle
    """
    return {"is_scraping": is_scraping}


@router.get("/scrape")
@router.post("/scrape")
async def trigger_scrape(token: str, background_tasks: BackgroundTasks):
    """
    Manually trigger a scrape run via HTTP.

    Query parameter:
    - token: Must match ADMIN_TOKEN environment variable

    Returns 403 Forbidden if token doesn't match.
    If token is correct, returns an HTML page that monitors scrape progress.
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
    background_tasks.add_task(scrape_with_status)
    logger.info("Manual scrape triggered via admin endpoint")

    # Return an HTML page that polls for status
    from fastapi.responses import HTMLResponse

    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Scrape Status</title>
        <style>
            body { font-family: Arial, sans-serif; padding: 20px; }
            .status { font-size: 18px; padding: 20px; border-radius: 5px; }
            .running { background-color: #fff3cd; color: #856404; }
            .finished { background-color: #d4edda; color: #155724; }
        </style>
    </head>
    <body>
        <h1>Coffee Scrape Monitor</h1>
        <div id="status" class="status running">
            ⏳ Scrape is running... This may take a few minutes.
        </div>
        <script>
            async function checkStatus() {
                try {
                    const response = await fetch('/api/admin/scrape/status');
                    const data = await response.json();
                    const statusDiv = document.getElementById('status');

                    if (data.is_scraping) {
                        statusDiv.textContent = '⏳ Scrape is running... This may take a few minutes.';
                        statusDiv.className = 'status running';
                        // Check again in 2 seconds
                        setTimeout(checkStatus, 2000);
                    } else {
                        statusDiv.textContent = '✅ Scrape finished! The catalog has been updated.';
                        statusDiv.className = 'status finished';
                    }
                } catch (error) {
                    console.error('Error checking status:', error);
                    setTimeout(checkStatus, 2000);
                }
            }

            // Check status every 2 seconds
            setTimeout(checkStatus, 2000);
        </script>
    </body>
    </html>
    """

    return HTMLResponse(content=html_content)
