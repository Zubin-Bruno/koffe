from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from koffe.db.database import get_db
from koffe.db.models import Roaster, ScrapeRun

router = APIRouter()


@router.get("/api/roasters", tags=["roasters"])
def list_roasters(db: Session = Depends(get_db)):
    roasters = db.query(Roaster).order_by(Roaster.name).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "slug": r.slug,
            "website_url": r.website_url,
            "country": r.country,
            "scraper_module": r.scraper_module,
            "scrape_interval_hours": r.scrape_interval_hours,
            "is_active": r.is_active,
        }
        for r in roasters
    ]


@router.get("/api/roasters/{roaster_id}/runs", tags=["roasters"])
def get_scrape_runs(roaster_id: int, limit: int = 10, db: Session = Depends(get_db)):
    runs = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.roaster_id == roaster_id)
        .order_by(ScrapeRun.started_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "coffees_found": r.coffees_found,
            "error_message": r.error_message,
        }
        for r in runs
    ]
