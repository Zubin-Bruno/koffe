from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from koffe.db.database import get_db
from koffe.db.models import Coffee, Roaster

router = APIRouter()


def _parse_int(val: str | None) -> int | None:
    """Convert a query param string to int, returning None for empty/invalid."""
    if not val:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


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
        "variety": c.variety,
        "altitude_masl": c.altitude_masl,
        "attributes": c.attributes or {},
        "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
        "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
    }


@router.get("/api/coffees", tags=["coffees"])
def list_coffees(
    roaster_id: str | None = Query(None),
    origin: str | None = Query(None),
    process: str | None = Query(None),
    acidity_min: str | None = Query(None),
    acidity_max: str | None = Query(None),
    sweetness_min: str | None = Query(None),
    sweetness_max: str | None = Query(None),
    body_min: str | None = Query(None),
    body_max: str | None = Query(None),
    available_only: str | None = Query(None),
    min_price: str | None = Query(None),
    max_price: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    roaster_id_int = _parse_int(roaster_id)
    acidity_min_int = _parse_int(acidity_min)
    acidity_max_int = _parse_int(acidity_max)
    sweetness_min_int = _parse_int(sweetness_min)
    sweetness_max_int = _parse_int(sweetness_max)
    body_min_int = _parse_int(body_min)
    body_max_int = _parse_int(body_max)
    min_price_int = _parse_int(min_price)
    max_price_int = _parse_int(max_price)

    q = db.query(Coffee)

    if available_only is None or available_only == "true":
        q = q.filter(Coffee.is_available == True)
    if roaster_id_int:
        q = q.filter(Coffee.roaster_id == roaster_id_int)
    if origin:
        q = q.filter(Coffee.origin_country.ilike(f"%{origin}%"))
    if process:
        q = q.filter(Coffee.process.ilike(f"%{process}%"))
    if acidity_min_int:
        q = q.filter(Coffee.acidity >= acidity_min_int)
    if acidity_max_int:
        q = q.filter(Coffee.acidity <= acidity_max_int)
    if sweetness_min_int:
        q = q.filter(Coffee.sweetness >= sweetness_min_int)
    if sweetness_max_int:
        q = q.filter(Coffee.sweetness <= sweetness_max_int)
    if body_min_int:
        q = q.filter(Coffee.body >= body_min_int)
    if body_max_int:
        q = q.filter(Coffee.body <= body_max_int)
    if min_price_int is not None:
        q = q.filter(Coffee.price_cents >= min_price_int)
    if max_price_int is not None:
        q = q.filter(Coffee.price_cents <= max_price_int)

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


@router.get("/coffees/{coffee_id}/detail", response_class=HTMLResponse, include_in_schema=False)
async def coffee_detail(coffee_id: int, request: Request, db: Session = Depends(get_db)):
    """HTMX partial — returns the coffee detail modal."""
    from fastapi import HTTPException

    coffee = db.query(Coffee).filter(Coffee.id == coffee_id).first()
    if not coffee:
        raise HTTPException(status_code=404, detail="Coffee not found")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "coffee_detail.html",
        {"request": request, "coffee": _coffee_to_dict(coffee)},
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(
    request: Request,
    origin: str | None = None,
    process: str | None = None,
    roaster_id: str | None = None,
    acidity_min: str | None = None,
    acidity_max: str | None = None,
    sweetness_min: str | None = None,
    sweetness_max: str | None = None,
    body_min: str | None = None,
    body_max: str | None = None,
    available_only: str | None = None,
    db: Session = Depends(get_db),
):
    roaster_id_int = _parse_int(roaster_id)
    acidity_min_int = _parse_int(acidity_min)
    acidity_max_int = _parse_int(acidity_max)
    sweetness_min_int = _parse_int(sweetness_min)
    sweetness_max_int = _parse_int(sweetness_max)
    body_min_int = _parse_int(body_min)
    body_max_int = _parse_int(body_max)

    q = db.query(Coffee)
    if available_only is None or available_only == "true":
        q = q.filter(Coffee.is_available == True)
    if origin:
        q = q.filter(Coffee.origin_country.ilike(f"%{origin}%"))
    if process:
        q = q.filter(Coffee.process.ilike(f"%{process}%"))
    if roaster_id_int:
        q = q.filter(Coffee.roaster_id == roaster_id_int)
    if acidity_min_int:
        q = q.filter(Coffee.acidity >= acidity_min_int)
    if acidity_max_int:
        q = q.filter(Coffee.acidity <= acidity_max_int)
    if sweetness_min_int:
        q = q.filter(Coffee.sweetness >= sweetness_min_int)
    if sweetness_max_int:
        q = q.filter(Coffee.sweetness <= sweetness_max_int)
    if body_min_int:
        q = q.filter(Coffee.body >= body_min_int)
    if body_max_int:
        q = q.filter(Coffee.body <= body_max_int)

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
                "roaster_id": roaster_id_int or "",
                "acidity_min": acidity_min_int or "",
                "acidity_max": acidity_max_int or "",
                "sweetness_min": sweetness_min_int or "",
                "sweetness_max": sweetness_max_int or "",
                "body_min": body_min_int or "",
                "body_max": body_max_int or "",
                "available_only": available_only is None or available_only == "true",
            },
            "total": len(coffees),
        },
    )


@router.get("/coffees", response_class=HTMLResponse, include_in_schema=False)
async def coffees_partial(
    request: Request,
    origin: str | None = None,
    process: str | None = None,
    roaster_id: str | None = None,
    acidity_min: str | None = None,
    acidity_max: str | None = None,
    sweetness_min: str | None = None,
    sweetness_max: str | None = None,
    body_min: str | None = None,
    body_max: str | None = None,
    available_only: str | None = None,
    db: Session = Depends(get_db),
):
    """HTMX partial — returns only the coffee card grid."""
    roaster_id_int = _parse_int(roaster_id)
    acidity_min_int = _parse_int(acidity_min)
    acidity_max_int = _parse_int(acidity_max)
    sweetness_min_int = _parse_int(sweetness_min)
    sweetness_max_int = _parse_int(sweetness_max)
    body_min_int = _parse_int(body_min)
    body_max_int = _parse_int(body_max)

    q = db.query(Coffee)
    if available_only is None or available_only == "true":
        q = q.filter(Coffee.is_available == True)
    if origin:
        q = q.filter(Coffee.origin_country.ilike(f"%{origin}%"))
    if process:
        q = q.filter(Coffee.process.ilike(f"%{process}%"))
    if roaster_id_int:
        q = q.filter(Coffee.roaster_id == roaster_id_int)
    if acidity_min_int:
        q = q.filter(Coffee.acidity >= acidity_min_int)
    if acidity_max_int:
        q = q.filter(Coffee.acidity <= acidity_max_int)
    if sweetness_min_int:
        q = q.filter(Coffee.sweetness >= sweetness_min_int)
    if sweetness_max_int:
        q = q.filter(Coffee.sweetness <= sweetness_max_int)
    if body_min_int:
        q = q.filter(Coffee.body >= body_min_int)
    if body_max_int:
        q = q.filter(Coffee.body <= body_max_int)

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
