"""Aplicação FastAPI: API JSON + dashboard HTML dos apartamentos ranqueados."""

from __future__ import annotations

from contextlib import asynccontextmanager
from math import ceil
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlmodel import select

from housing_radar.config import get_settings
from housing_radar.db import init_db, session_scope
from housing_radar.models import Listing

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# --- Formatação pt-BR (filtros Jinja) ---------------------------------------
def _int_br(value: float | int | None) -> str | None:
    """185000 -> '185.000' (separador de milhar pt-BR)."""
    if value is None:
        return None
    return f"{int(round(value)):,}".replace(",", ".")


def _dec_br(value: float | int | None, places: int = 1) -> str | None:
    """6.5 -> '6,5' (decimal pt-BR, com milhar). Usa um sentinela p/ trocar . e ,."""
    if value is None:
        return None
    s = f"{value:,.{places}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


templates.env.filters["int_br"] = _int_br
templates.env.filters["dec_br"] = _dec_br


# --- Ordenação --------------------------------------------------------------
# (None sempre por último; maior score/área primeiro, menor preço/tempo primeiro.)
_SORTS = {
    "score": lambda x: (x.score is None, -(x.score or 0)),
    "price": lambda x: (x.price is None, x.price or 0),
    "car": lambda x: (x.time_car_min is None, x.time_car_min or 0),
    "area": lambda x: (x.area_m2 is None, -(x.area_m2 or 0)),
}
_SORT_LABELS = {
    "score": "Score (maior primeiro)",
    "price": "Preço (menor primeiro)",
    "car": "Tempo de carro (menor primeiro)",
    "area": "Área (maior primeiro)",
}

# --- Paginação --------------------------------------------------------------
_PER_PAGE_OPTIONS = [10, 20, 30]
_PER_PAGE_DEFAULT = 20


def _parse_per_page(value: str | None) -> int | str:
    """'10'/'20'/'30' -> int; 'all' -> 'all'; resto -> default."""
    if value == "all":
        return "all"
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _PER_PAGE_DEFAULT
    return n if n in _PER_PAGE_OPTIONS else _PER_PAGE_DEFAULT


def _page_window(page: int, total_pages: int, span: int = 2) -> list[int | None]:
    """Números de página a exibir (com None marcando reticências)."""
    wanted = {1, total_pages}
    for p in range(page - span, page + span + 1):
        if 1 <= p <= total_pages:
            wanted.add(p)
    out: list[int | None] = []
    prev = 0
    for p in sorted(wanted):
        if p - prev > 1:
            out.append(None)
        out.append(p)
        prev = p
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="UFSCar Housing Radar", version="0.1.0", lifespan=lifespan)


def _query_listings(
    *,
    q: str | None = None,
    max_price: float | None = None,
    min_bedrooms: int | None = None,
    min_area: float | None = None,
    max_car_min: float | None = None,
    neighborhood: str | None = None,
    sort: str = "score",
) -> list[Listing]:
    """Lista completa (filtrada + ordenada). O fatiamento fica com quem chama."""
    with session_scope() as session:
        stmt = select(Listing).where(Listing.status == "active")
        if max_price is not None:
            stmt = stmt.where(Listing.price <= max_price)
        if min_bedrooms is not None:
            stmt = stmt.where(Listing.bedrooms >= min_bedrooms)
        if min_area is not None:
            stmt = stmt.where(Listing.area_m2 >= min_area)
        if max_car_min is not None:
            stmt = stmt.where(Listing.time_car_min <= max_car_min)
        if neighborhood:
            stmt = stmt.where(Listing.neighborhood.ilike(f"%{neighborhood}%"))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(Listing.title.ilike(like), Listing.neighborhood.ilike(like))
            )
        listings = session.exec(stmt).all()

    listings.sort(key=_SORTS.get(sort, _SORTS["score"]))
    return listings


def _all_neighborhoods() -> list[str]:
    """Bairros distintos (para autocompletar a busca)."""
    with session_scope() as session:
        rows = session.exec(
            select(Listing.neighborhood).where(Listing.status == "active")
        ).all()
    return sorted({r for r in rows if r})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/listings")
def api_listings(
    q: str | None = Query(default=None),
    max_price: float | None = Query(default=None),
    min_bedrooms: int | None = Query(default=None),
    min_area: float | None = Query(default=None),
    max_car_min: float | None = Query(default=None),
    neighborhood: str | None = Query(default=None),
    sort: str = Query(default="score"),
    limit: int = Query(default=100, le=1000),
) -> JSONResponse:
    listings = _query_listings(
        q=q,
        max_price=max_price,
        min_bedrooms=min_bedrooms,
        min_area=min_area,
        max_car_min=max_car_min,
        neighborhood=neighborhood,
        sort=sort,
    )
    return JSONResponse([item.model_dump(mode="json") for item in listings[:limit]])


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str | None = Query(default=None),
    max_price: float | None = Query(default=None),
    min_bedrooms: int | None = Query(default=None),
    min_area: float | None = Query(default=None),
    max_car_min: float | None = Query(default=None),
    neighborhood: str | None = Query(default=None),
    sort: str = Query(default="score"),
    page: int = Query(default=1, ge=1),
    per_page: str = Query(default=str(_PER_PAGE_DEFAULT)),
) -> HTMLResponse:
    settings = get_settings()
    sort = sort if sort in _SORTS else "score"
    per = _parse_per_page(per_page)

    listings = _query_listings(
        q=q,
        max_price=max_price,
        min_bedrooms=min_bedrooms,
        min_area=min_area,
        max_car_min=max_car_min,
        neighborhood=neighborhood,
        sort=sort,
    )

    total = len(listings)
    if not isinstance(per, int) or total == 0:
        page, total_pages, page_items = 1, 1, listings
        first = 1 if total else 0
    else:
        total_pages = max(1, ceil(total / per))
        page = min(max(1, page), total_pages)
        start = (page - 1) * per
        page_items = listings[start : start + per]
        first = start + 1

    # querystring base (preserva filtros/sort/per_page; o page é anexado nos links)
    base_params: dict[str, str | int] = {"sort": sort, "per_page": per_page}
    for key, value in (
        ("q", q or neighborhood or None),
        ("max_price", int(max_price) if max_price else None),
        ("min_bedrooms", min_bedrooms),
        ("min_area", int(min_area) if min_area else None),
        ("max_car_min", int(max_car_min) if max_car_min else None),
    ):
        if value not in (None, ""):
            base_params[key] = value

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "listings": page_items,
            "destination": settings.destination_label,
            "neighborhoods": _all_neighborhoods(),
            "sort": sort,
            "sort_labels": _SORT_LABELS,
            "filters": {
                "q": q or neighborhood or "",
                "max_price": max_price,
                "min_bedrooms": min_bedrooms,
                "min_area": min_area,
                "max_car_min": max_car_min,
            },
            "pagination": {
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "per_page": per_page,  # string original ("20" ou "all")
                "per_page_options": _PER_PAGE_OPTIONS,
                "first": first,
                "last": first + len(page_items) - 1 if page_items else 0,
                "pages": _page_window(page, total_pages),
                "query_base": urlencode(base_params),
            },
        },
    )
