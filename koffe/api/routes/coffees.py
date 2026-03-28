import unicodedata

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import cast, String, or_, func, text
from sqlalchemy.orm import Session

from koffe.db.database import get_db
from koffe.db.models import Coffee, Roaster

router = APIRouter()


def _strip_accents(text: str) -> str:
    """Remove accent marks from text for accent-insensitive matching.

    Example: 'Azúcar' → 'Azucar', 'café' → 'cafe'.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")


def _parse_int(val: str | None) -> int | None:
    """Convert a query param string to int, returning None for empty/invalid."""
    if not val:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _has_any_filter(origin, process, roaster_id, variety, brew_method,
                    acidity_min, acidity_max, sweetness_min,
                    sweetness_max, body_min, body_max, search=None,
                    tasting_notes=None) -> bool:
    """Return True if the user has set at least one filter."""
    return any([origin, process, roaster_id, variety, brew_method,
                acidity_min, acidity_max, sweetness_min,
                sweetness_max, body_min, body_max, search, tasting_notes])


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
        "brew_methods": c.brew_methods or [],
        "attributes": c.attributes or {},
        "first_seen_at": c.first_seen_at.isoformat() if c.first_seen_at else None,
        "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
    }


def _apply_filters(q, origin, process, roaster_id_int, acidity_min_int,
                   acidity_max_int, sweetness_min_int, sweetness_max_int,
                   body_min_int, body_max_int, variety, brew_method,
                   search=None, tasting_notes=None):
    """Apply all filter conditions to a query. Returns the filtered query.

    Text filters use func.strip_accents() (registered in database.py) so that
    accented characters are ignored during matching — e.g. searching "azucar"
    will match "Azúcar".
    """
    if origin:
        q = q.filter(or_(*[func.strip_accents(Coffee.origin_country).ilike(f"%{_strip_accents(o)}%") for o in origin]))
    if process:
        q = q.filter(or_(*[func.strip_accents(Coffee.process).ilike(f"%{_strip_accents(p)}%") for p in process]))
    if roaster_id_int:
        q = q.filter(Coffee.roaster_id.in_(roaster_id_int))
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
    if variety:
        q = q.filter(or_(*[func.strip_accents(Coffee.variety).ilike(f"%{_strip_accents(v)}%") for v in variety]))
    if brew_method:
        q = q.filter(or_(*[func.strip_accents(cast(Coffee.brew_methods, String)).ilike(f"%{_strip_accents(m)}%") for m in brew_method]))
    if search:
        term = f"%{_strip_accents(search)}%"
        q = q.filter(or_(
            func.strip_accents(Coffee.name).ilike(term),
            func.strip_accents(Coffee.description).ilike(term),
            func.strip_accents(cast(Coffee.attributes, String)).ilike(term),
        ))
    if tasting_notes:
        # OR logic: coffee matches if it has ANY of the selected notes.
        # Uses json_each() to search inside the JSON array stored in
        # attributes->'tasting_notes'.  Accent-insensitive via strip_accents.
        note_clauses = []
        for note in tasting_notes:
            clean = _strip_accents(note).lower()
            clause = text(
                "EXISTS (SELECT 1 FROM json_each("
                "json_extract(coffees.attributes, '$.tasting_notes')) "
                "WHERE strip_accents(LOWER(value)) = :note)"
            ).bindparams(note=clean)
            note_clauses.append(clause)
        q = q.filter(or_(*note_clauses))
    return q


def _get_filter_options(db: Session) -> dict:
    """Load all dropdown options for the filter UI from the DB.

    Returns a dict with keys: roasters, origins, processes, varieties,
    brew_methods_options, tasting_notes_options.  Used by the explorer page
    and by the comparison search partial so the same dropdowns appear in both.
    """
    roasters = db.query(Roaster).filter(Roaster.is_active == True).all()
    all_origins = sorted([r[0] for r in db.query(Coffee.origin_country).filter(Coffee.is_available == True).distinct() if r[0]])
    all_processes = sorted([r[0] for r in db.query(Coffee.process).filter(Coffee.is_available == True).distinct() if r[0]])
    all_varieties = sorted([r[0] for r in db.query(Coffee.variety).filter(Coffee.is_available == True).distinct() if r[0]])

    rows = db.query(Coffee.brew_methods).filter(Coffee.is_available == True).all()
    brew_set = set()
    for (bm,) in rows:
        if bm:
            brew_set.update(bm)

    # Extract unique tasting notes from attributes JSON
    attr_rows = db.query(Coffee.attributes).filter(
        Coffee.is_available == True,
        Coffee.attributes.isnot(None),
    ).all()
    notes_set: set[str] = set()
    for (attrs,) in attr_rows:
        if attrs and isinstance(attrs, dict):
            raw_notes = attrs.get("tasting_notes", [])
            if isinstance(raw_notes, list):
                for n in raw_notes:
                    if isinstance(n, str) and n.strip():
                        # Normalize: first letter uppercase, rest lowercase
                        notes_set.add(n.strip().capitalize())

    return {
        "roasters": roasters,
        "origins": all_origins,
        "processes": all_processes,
        "varieties": all_varieties,
        "brew_methods_options": sorted(brew_set),
        "tasting_notes_options": sorted(notes_set),
    }


@router.get("/api/coffees", tags=["coffees"])
def list_coffees(
    roaster_id: list[str] = Query(default=[]),
    origin: list[str] = Query(default=[]),
    process: list[str] = Query(default=[]),
    acidity_min: str | None = Query(None),
    acidity_max: str | None = Query(None),
    sweetness_min: str | None = Query(None),
    sweetness_max: str | None = Query(None),
    body_min: str | None = Query(None),
    body_max: str | None = Query(None),
    variety: list[str] = Query(default=[]),
    brew_method: list[str] = Query(default=[]),
    search: str | None = Query(None),
    tasting_note: list[str] = Query(default=[]),
    available_only: str | None = Query(None),
    min_price: str | None = Query(None),
    max_price: str | None = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
    db: Session = Depends(get_db),
):
    roaster_id_int = [_parse_int(r) for r in roaster_id if _parse_int(r) is not None]
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

    q = _apply_filters(q, origin, process, roaster_id_int,
                       acidity_min_int, acidity_max_int,
                       sweetness_min_int, sweetness_max_int,
                       body_min_int, body_max_int,
                       variety, brew_method, search, tasting_note)

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
async def landing(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/buscar", response_class=HTMLResponse, include_in_schema=False)
async def index(
    request: Request,
    origin: list[str] = Query(default=[]),
    process: list[str] = Query(default=[]),
    roaster_id: list[str] = Query(default=[]),
    acidity_min: str | None = None,
    acidity_max: str | None = None,
    sweetness_min: str | None = None,
    sweetness_max: str | None = None,
    body_min: str | None = None,
    body_max: str | None = None,
    variety: list[str] = Query(default=[]),
    brew_method: list[str] = Query(default=[]),
    search: str | None = None,
    tasting_note: list[str] = Query(default=[]),
    include_incomplete: bool = Query(default=False),
    min_price: str | None = None,
    max_price: str | None = None,
    db: Session = Depends(get_db),
):
    roaster_id_int = [_parse_int(r) for r in roaster_id if _parse_int(r) is not None]
    acidity_min_int = _parse_int(acidity_min)
    acidity_max_int = _parse_int(acidity_max)
    sweetness_min_int = _parse_int(sweetness_min)
    sweetness_max_int = _parse_int(sweetness_max)
    body_min_int = _parse_int(body_min)
    body_max_int = _parse_int(body_max)
    min_price_int = _parse_int(min_price)
    max_price_int = _parse_int(max_price)

    has_filters = _has_any_filter(
        origin, process, roaster_id_int, variety, brew_method,
        acidity_min_int, acidity_max_int, sweetness_min_int,
        sweetness_max_int, body_min_int, body_max_int, search,
        tasting_note,
    ) or include_incomplete or min_price_int is not None or max_price_int is not None

    # Check if "show all" was requested via query param
    show_all = request.query_params.get("show_all") == "true"

    total_available = db.query(Coffee).filter(Coffee.is_available == True).count()

    if show_all:
        q = db.query(Coffee).filter(Coffee.is_available == True)
        coffees = q.join(Roaster).order_by(Roaster.name, Coffee.name).all()
    elif has_filters:
        q = db.query(Coffee).filter(Coffee.is_available == True)
        if not include_incomplete:
             pass

        q = _apply_filters(q, origin, process, roaster_id_int,
                           acidity_min_int, acidity_max_int,
                           sweetness_min_int, sweetness_max_int,
                           body_min_int, body_max_int,
                           variety, brew_method, search, tasting_note)
        if min_price_int is not None:
            q = q.filter(Coffee.price_cents >= min_price_int * 100)
        if max_price_int is not None:
            q = q.filter(Coffee.price_cents <= max_price_int * 100)

        coffees = q.order_by(Coffee.name).limit(200).all()
    else:
        coffees = []

    opts = _get_filter_options(db)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "coffees": [_coffee_to_dict(c) for c in coffees],
            **opts,
            "filters": {
                "origin": origin,
                "process": process,
                "roaster_id": roaster_id_int,
                "acidity_min": acidity_min_int or "",
                "acidity_max": acidity_max_int or "",
                "sweetness_min": sweetness_min_int or "",
                "sweetness_max": sweetness_max_int or "",
                "body_min": body_min_int or "",
                "body_max": body_max_int or "",
                "variety": variety,
                "brew_method": brew_method,
                "search": search or "",
                "tasting_notes": tasting_note,
                "include_incomplete": include_incomplete,
            },
            "total": len(coffees),
            "total_available": total_available,
            "has_filters": has_filters,
            "show_all": show_all,
        },
    )


@router.get("/ia", response_class=HTMLResponse, include_in_schema=False)
async def explore_ia(request: Request, db: Session = Depends(get_db)):
    templates = request.app.state.templates
    # Provide an initial empty result set or some popular ones.
    # For now, we'll just send total count
    total_available = db.query(Coffee).filter(Coffee.is_available == True).count()
    return templates.TemplateResponse("explore_ia.html", {"request": request, "total_available": total_available})


@router.get("/coffees", response_class=HTMLResponse, include_in_schema=False)
async def coffees_partial(
    request: Request,
    origin: list[str] = Query(default=[]),
    process: list[str] = Query(default=[]),
    roaster_id: list[str] = Query(default=[]),
    acidity_min: str | None = None,
    acidity_max: str | None = None,
    sweetness_min: str | None = None,
    sweetness_max: str | None = None,
    body_min: str | None = None,
    body_max: str | None = None,
    variety: list[str] = Query(default=[]),
    brew_method: list[str] = Query(default=[]),
    search: str | None = None,
    tasting_note: list[str] = Query(default=[]),
    include_incomplete: bool = Query(default=False),
    min_price: str | None = None,
    max_price: str | None = None,
    db: Session = Depends(get_db),
):
    """HTMX partial — returns only the coffee card grid."""
    roaster_id_int = [_parse_int(r) for r in roaster_id if _parse_int(r) is not None]
    acidity_min_int = _parse_int(acidity_min)
    acidity_max_int = _parse_int(acidity_max)
    sweetness_min_int = _parse_int(sweetness_min)
    sweetness_max_int = _parse_int(sweetness_max)
    body_min_int = _parse_int(body_min)
    body_max_int = _parse_int(body_max)
    min_price_int = _parse_int(min_price)
    max_price_int = _parse_int(max_price)

    has_filters = _has_any_filter(
        origin, process, roaster_id_int, variety, brew_method,
        acidity_min_int, acidity_max_int, sweetness_min_int,
        sweetness_max_int, body_min_int, body_max_int, search,
        tasting_note,
    ) or include_incomplete or min_price_int is not None or max_price_int is not None

    # Check if "show all" was requested via query param
    show_all = request.query_params.get("show_all") == "true"

    if show_all:
        q = db.query(Coffee).filter(Coffee.is_available == True)
        coffees = q.join(Roaster).order_by(Roaster.name, Coffee.name).all()
    elif has_filters:
        q = db.query(Coffee).filter(Coffee.is_available == True)
        q = _apply_filters(q, origin, process, roaster_id_int,
                           acidity_min_int, acidity_max_int,
                           sweetness_min_int, sweetness_max_int,
                           body_min_int, body_max_int,
                           variety, brew_method, search, tasting_note)

        if min_price_int is not None:
            q = q.filter(Coffee.price_cents >= min_price_int * 100)
        if max_price_int is not None:
            q = q.filter(Coffee.price_cents <= max_price_int * 100)

        coffees = q.order_by(Coffee.name).limit(200).all()
    else:
        coffees = []

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "coffee_cards.html",
        {
            "request": request,
            "coffees": [_coffee_to_dict(c) for c in coffees],
            "total": len(coffees),
            "has_filters": has_filters,
            "show_all": show_all,
        },
    )


@router.get("/coffees/compare-search", response_class=HTMLResponse, include_in_schema=False)
async def compare_search(
    request: Request,
    slot: str = Query("0"),
    origin: list[str] = Query(default=[]),
    process: list[str] = Query(default=[]),
    roaster_id: list[str] = Query(default=[]),
    acidity_min: str | None = None,
    acidity_max: str | None = None,
    sweetness_min: str | None = None,
    sweetness_max: str | None = None,
    body_min: str | None = None,
    body_max: str | None = None,
    variety: list[str] = Query(default=[]),
    brew_method: list[str] = Query(default=[]),
    search: str | None = None,
    tasting_note: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    """HTMX partial — filter form + card grid for one comparison slot."""
    roaster_id_int = [_parse_int(r) for r in roaster_id if _parse_int(r) is not None]
    acidity_min_int = _parse_int(acidity_min)
    acidity_max_int = _parse_int(acidity_max)
    sweetness_min_int = _parse_int(sweetness_min)
    sweetness_max_int = _parse_int(sweetness_max)
    body_min_int = _parse_int(body_min)
    body_max_int = _parse_int(body_max)

    has_filters = _has_any_filter(
        origin, process, roaster_id_int, variety, brew_method,
        acidity_min_int, acidity_max_int, sweetness_min_int,
        sweetness_max_int, body_min_int, body_max_int, search,
        tasting_note,
    )

    if has_filters:
        q = db.query(Coffee).filter(Coffee.is_available == True)
        q = _apply_filters(q, origin, process, roaster_id_int,
                           acidity_min_int, acidity_max_int,
                           sweetness_min_int, sweetness_max_int,
                           body_min_int, body_max_int,
                           variety, brew_method, search, tasting_note)
        coffees = q.order_by(Coffee.name).limit(100).all()
    else:
        coffees = []

    opts = _get_filter_options(db)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "compare_search.html",
        {
            "request": request,
            "slot": slot,
            "coffees": [_coffee_to_dict(c) for c in coffees],
            "total": len(coffees),
            "has_filters": has_filters,
            "filters": {
                "origin": origin,
                "process": process,
                "roaster_id": roaster_id_int,
                "acidity_min": acidity_min_int or "",
                "acidity_max": acidity_max_int or "",
                "sweetness_min": sweetness_min_int or "",
                "sweetness_max": sweetness_max_int or "",
                "body_min": body_min_int or "",
                "body_max": body_max_int or "",
                "variety": variety,
                "brew_method": brew_method,
                "search": search or "",
                "tasting_notes": tasting_note,
            },
            **opts,
        },
    )


@router.get("/coffees/{coffee_id}/compare-detail", response_class=HTMLResponse, include_in_schema=False)
async def compare_detail(
    coffee_id: int,
    request: Request,
    slot: str = Query("0"),
    db: Session = Depends(get_db),
):
    """HTMX partial — selected coffee detail inside a comparison slot."""
    from fastapi import HTTPException

    coffee = db.query(Coffee).filter(Coffee.id == coffee_id).first()
    if not coffee:
        raise HTTPException(status_code=404, detail="Coffee not found")

    templates = request.app.state.templates
    return templates.TemplateResponse(
        "compare_detail.html",
        {"request": request, "coffee": _coffee_to_dict(coffee), "slot": slot},
    )
