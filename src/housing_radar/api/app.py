"""Aplicação FastAPI: API JSON + dashboard HTML dos apartamentos ranqueados."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from housing_radar.config import get_settings
from housing_radar.db import init_db, session_scope
from housing_radar.models import Listing

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="UFSCar Housing Radar", version="0.1.0", lifespan=lifespan)


def _query_listings(
    *,
    max_price: float | None,
    min_bedrooms: int | None,
    neighborhood: str | None,
    limit: int,
) -> list[Listing]:
    with session_scope() as session:
        stmt = select(Listing).where(Listing.status == "active")
        if max_price is not None:
            stmt = stmt.where(Listing.price <= max_price)
        if min_bedrooms is not None:
            stmt = stmt.where(Listing.bedrooms >= min_bedrooms)
        if neighborhood:
            stmt = stmt.where(Listing.neighborhood.ilike(f"%{neighborhood}%"))
        listings = session.exec(stmt).all()

    listings.sort(key=lambda x: (x.score is None, -(x.score or 0)))
    return listings[:limit]


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/listings")
def api_listings(
    max_price: float | None = Query(default=None),
    min_bedrooms: int | None = Query(default=None),
    neighborhood: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
) -> JSONResponse:
    listings = _query_listings(
        max_price=max_price,
        min_bedrooms=min_bedrooms,
        neighborhood=neighborhood,
        limit=limit,
    )
    return JSONResponse([item.model_dump(mode="json") for item in listings])


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    max_price: float | None = Query(default=None),
    min_bedrooms: int | None = Query(default=None),
    neighborhood: str | None = Query(default=None),
    limit: int = Query(default=100, le=1000),
) -> HTMLResponse:
    settings = get_settings()
    listings = _query_listings(
        max_price=max_price,
        min_bedrooms=min_bedrooms,
        neighborhood=neighborhood,
        limit=limit,
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "listings": listings,
            "destination": settings.destination_label,
            "filters": {
                "max_price": max_price,
                "min_bedrooms": min_bedrooms,
                "neighborhood": neighborhood,
            },
        },
    )
