from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from koffe.db.database import get_db
from koffe.db.models import Coffee, Roaster

router = APIRouter()


def _coffee_to_dict(c: Coffee) -> dict:
    return {
        "id": c.id,
        "roaster_id": c.roaster_id,
        "roaster_name": c.roaster.name if c.roaster else None,
        "name": c.name,
        "url": c.url,
        "price_cents": c.price_cents,
        "price_display": f"${c.price_cents / 100:,.0f}" if c.price_cents else None,
        "currency": c.currency,
        "weight_grams": c.weight_grams,
        "is_available": c.is_available,
        "image_url": c.image_url,
        "description": c.description,
        "origin_country": c.origin_country,
        "process": c.process,
        "roast_level": c.roast_level,
        "acidity": c.acidity,
        "sweetness": c.sweetness,
        "body": c.body,
        "attributes": c.attributes or {},
        "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
        "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
    }


@router.get("/api/coffees", tags=["coffees"])
def list_coffees(
    roaster_id: int | None = Query(None),
    origin: str | None = Query(None),
    process: str | None = Query(None),
    acidity_min: int | None = Query(None, ge=1, le=5),
    acidity_max: int | None = Query(None, ge=1, le=5),
    sweetness_min: int | None = Query(None, ge=1, le=5),
    sweetness_max: int | None = Query(None, ge=1, le=5),
    body_min: int | None = Query(None, ge=1, le=5),
    body_max: int | None = Query(None, ge=1, le=5),
    available_only: str | None = Query(None),
    min_price: int | None = Query(None, description="Minimum price in cents"),
    max_price: int | None = Query(None, description="Maximum price in cents"),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    q = db.query(Coffee)

    if available_only != "false":
        q = q.filter(Coffee.is_available == True)
    if roaster_id:
        q = q.filter(Coffee.roaster_id == roaster_id)
    if origin:
        q = q.filter(Coffee.origin_country.ilike(f"%{origin}%"))
    if process:
        q = q.filter(Coffee.process.ilike(f"%{process}%"))
    if acidity_min:
        q = q.filter(Coffee.acidity >= acidity_min)
    if acidity_max:
        q = q.filter(Coffee.acidity <= acidity_max)
    if sweetness_min:
        q = q.filter(Coffee.sweetness >= sweetness_min)
    if sweetness_max:
        q = q.filter(Coffee.sweetness <= sweetness_max)
    if body_min:
        q = q.filter(Coffee.body >= body_min)
    if body_max:
        q = q.filter(Coffee.body <= body_max)
    if min_price is not None:
        q = q.filter(Coffee.price_cents >= min_price)
    if max_price is not None:
        q = q.filter(Coffee.price_cents <= max_price)

    total = q.count()
    coffees = q.order_by(Coffee.name).offset(offset).limit(limit).all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "results": [_coffee_to_dict(c) for c in coffees],
    }


@router.get("/api/coffees/{coffee_id}", tags=["coffees"])
def get_coffee(coffee_id: int, db: Session = Depends(get_db)):
    from fastapi import HTTPException

    coffee = db.query(Coffee).filter(Coffee.id == coffee_id).first()
    if not coffee:
        raise HTTPException(status_code=404, detail="Coffee not found")
    return _coffee_to_dict(coffee)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(
    request: Request,
    origin: str | None = None,
    process: str | None = None,
    roaster_id: int | None = None,
    acidity_min: int | None = None,
    acidity_max: int | None = None,
    sweetness_min: int | None = None,
    sweetness_max: int | None = None,
    body_min: int | None = None,
    body_max: int | None = None,
    available_only: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(Coffee)
    if available_only != "false":
        q = q.filter(Coffee.is_available == True)
    if origin:
        q = q.filter(Coffee.origin_country.ilike(f"%{origin}%"))
    if process:
        q = q.filter(Coffee.process.ilike(f"%{process}%"))
    if roaster_id:
        q = q.filter(Coffee.roaster_id == roaster_id)
    if acidity_min:
        q = q.filter(Coffee.acidity >= acidity_min)
    if acidity_max:
        q = q.filter(Coffee.acidity <= acidity_max)
    if sweetness_min:
        q = q.filter(Coffee.sweetness >= sweetness_min)
    if sweetness_max:
        q = q.filter(Coffee.sweetness <= sweetness_max)
    if body_min:
        q = q.filter(Coffee.body >= body_min)
    if body_max:
        q = q.filter(Coffee.body <= body_max)

    coffees = q.order_by(Coffee.name).limit(200).all()
    roasters = db.query(Roaster).filter(Roaster.is_active == True).all()

    # Get distinct filter options from DB
    all_origins = [r[0] for r in db.query(Coffee.origin_country).distinct() if r[0]]
    all_processes = [r[0] for r in db.query(Coffee.process).distinct() if r[0]]

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "coffees": [_coffee_to_dict(c) for c in coffees],
            "roasters": roasters,
            "origins": sorted(all_origins),
            "processes": sorted(all_processes),
            "filters": {
                "origin": origin or "",
                "process": process or "",
                "roaster_id": roaster_id or "",
                "acidity_min": acidity_min or "",
                "acidity_max": acidity_max or "",
                "sweetness_min": sweetness_min or "",
                "sweetness_max": sweetness_max or "",
                "body_min": body_min or "",
                "body_max": body_max or "",
                "available_only": available_only != "false",
            },
            "total": len(coffees),
        },
    )


@router.get("/coffees", response_class=HTMLResponse, include_in_schema=False)
async def coffees_partial(
    request: Request,
    origin: str | None = None,
    process: str | None = None,
    roaster_id: int | None = None,
    acidity_min: int | None = None,
    acidity_max: int | None = None,
    sweetness_min: int | None = None,
    sweetness_max: int | None = None,
    body_min: int | None = None,
    body_max: int | None = None,
    available_only: str | None = None,
    db: Session = Depends(get_db),
):
    """HTMX partial — returns only the coffee card grid."""
    q = db.query(Coffee)
    if available_only != "false":
        q = q.filter(Coffee.is_available == True)
    if origin:
        q = q.filter(Coffee.origin_country.ilike(f"%{origin}%"))
    if process:
        q = q.filter(Coffee.process.ilike(f"%{process}%"))
    if roaster_id:
        q = q.filter(Coffee.roaster_id == roaster_id)
    if acidity_min:
        q = q.filter(Coffee.acidity >= acidity_min)
    if acidity_max:
        q = q.filter(Coffee.acidity <= acidity_max)
    if sweetness_min:
        q = q.filter(Coffee.sweetness >= sweetness_min)
    if sweetness_max:
        q = q.filter(Coffee.sweetness <= sweetness_max)
    if body_min:
        q = q.filter(Coffee.body >= body_min)
    if body_max:
        q = q.filter(Coffee.body <= body_max)

    coffees = q.order_by(Coffee.name).limit(200).all()

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "coffee_cards.html",
        {
            "request": request,
            "coffees": [_coffee_to_dict(c) for c in coffees],
            "total": len(coffees),
        },
    )
