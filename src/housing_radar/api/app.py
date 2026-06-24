"""Aplicação FastAPI: API JSON + dashboard HTML dos apartamentos ranqueados."""

from __future__ import annotations

import json
import re
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlmodel import select

from housing_radar.config import get_settings
from housing_radar.db import init_db, session_scope
from housing_radar.models import Listing

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# --- Coerção de query params vindos de formulário (string vazia -> None) -----
def _to_float(value: str | None) -> float | None:
    """'' / None / inválido -> None; senão float."""
    if value is None or not str(value).strip():
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _extract_id(value: str | None) -> str | None:
    """Pega o último grupo de dígitos de uma busca/URL colada.

    Ex.: 'https://iplano.com.br/imovel/.../jardim-jockei-club-a/18219/' -> '18219'.
    """
    if not value:
        return None
    matches = re.findall(r"\d+", value)
    return matches[-1] if matches else None


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

# --- Dedup entre fontes (o mesmo imóvel em VivaReal/ZAP/imovelweb/imobiliária) -
def _norm_txt(value: str | None) -> str:
    nfkd = unicodedata.normalize("NFKD", (value or "").lower())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def _dup_signature(listing: Listing):
    """Assinatura para juntar o mesmo imóvel anunciado em fontes diferentes.

    Bairro + quartos + área (m²) + preço arredondado a R$5k. Sem dados-chave,
    não agrupa (fica único pelo id) — conservador para não fundir o que não dá.
    """
    hood = _norm_txt(listing.neighborhood)
    if not (hood and listing.area_m2 and listing.price):
        return ("uniq", listing.id)
    return (hood, listing.bedrooms or 0, round(listing.area_m2), round(listing.price / 5000) * 5000)


def _hi_res(url: str) -> str:
    """Eleva a foto à melhor resolução disponível (as fontes servem thumb por padrão)."""
    if "msys-imob" in url:  # MSYS: sufixo 'AT' é miniatura; sem ele vem o original
        return re.sub(r"AT\.(jpe?g|png|webp)$", r".\1", url, flags=re.I)
    if "resizedimgs" in url:  # Grupo ZAP redimensiona via querystring
        return re.sub(r"dimension=\d+x\d+", "dimension=1920x1080", url)
    if "objectstorage" in url and "_thumb/" in url:  # Cardinali (OCI): foto_thumb -> foto_
        return url.replace("_thumb/", "_/")
    return url


def _photos(listing: Listing) -> list[str]:
    """URLs de foto (em alta) a partir do dado cru (MSYS: jsonPhotos; demais: image)."""
    raw = listing.raw or {}
    urls: list[str] = []
    jp = raw.get("jsonPhotos")
    if isinstance(jp, str):  # MSYS (detalhe) guarda como string JSON
        try:
            jp = json.loads(jp)
        except (ValueError, TypeError):
            jp = None
    if isinstance(jp, list):
        urls = [p["urlPhoto"] for p in jp if isinstance(p, dict) and p.get("urlPhoto")]
    if not urls:
        img = raw.get("image")
        if isinstance(img, list):
            urls = [u for u in img if isinstance(u, str)]
        elif isinstance(img, str):
            urls = [img]
    return [_hi_res(u) for u in urls]


def _collapse_duplicates(listings: list[Listing], sort_key):
    """Colapsa duplicatas: 1 representante por grupo (o melhor pelo sort atual)."""
    groups: dict = {}
    for item in listings:
        groups.setdefault(_dup_signature(item), []).append(item)
    reps: list[Listing] = []
    meta: dict[int, dict] = {}
    for members in groups.values():
        members.sort(key=sort_key)
        rep = members[0]
        reps.append(rep)
        if len(members) > 1:
            prices = sorted({round(m.price) for m in members if m.price})
            meta[rep.id] = {
                "count": len(members),
                "sources": sorted({m.source for m in members}),
                "price_min": prices[0] if prices else None,
                "price_max": prices[-1] if prices else None,
            }
    reps.sort(key=sort_key)
    return reps, meta


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="UFSCar Housing Radar", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(_STATIC_DIR / "favicon.ico")


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
            term = q.strip()
            like = f"%{term}%"
            conds = [
                Listing.title.ilike(like),
                Listing.neighborhood.ilike(like),
                Listing.url.ilike(like),
                Listing.source_id.ilike(like),
            ]
            # Se colaram uma URL (ou ID), casa o ID do anúncio exatamente.
            found_id = _extract_id(term)
            if found_id:
                conds.append(Listing.source_id == found_id)
            stmt = stmt.where(or_(*conds))
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
    # Aceitos como string p/ tolerar campos vazios de formulário ("" -> sem filtro).
    max_price: str | None = Query(default=None),
    min_bedrooms: str | None = Query(default=None),
    min_area: str | None = Query(default=None),
    max_car_min: str | None = Query(default=None),
    neighborhood: str | None = Query(default=None),
    sort: str = Query(default="score"),
    group: str = Query(default="1"),
    limit: int = Query(default=5000, le=5000),
) -> JSONResponse:
    sort = sort if sort in _SORTS else "score"
    listings = _query_listings(
        q=q,
        max_price=_to_float(max_price),
        min_bedrooms=_to_int(min_bedrooms),
        min_area=_to_float(min_area),
        max_car_min=_to_float(max_car_min),
        neighborhood=neighborhood,
        sort=sort,
    )
    dup_meta: dict[int, dict] = {}
    if group != "0":
        listings, dup_meta = _collapse_duplicates(listings, _SORTS[sort])

    out = []
    for item in listings[:limit]:
        data = item.model_dump(mode="json")
        data.pop("raw", None)  # raw é pesado; a lista não precisa
        meta = dup_meta.get(item.id)
        data["dup_count"] = meta["count"] if meta else 1
        data["dup_sources"] = meta["sources"] if meta else [item.source]
        out.append(data)
    return JSONResponse(out)


@app.get("/api/listings/{listing_id}")
def api_listing_detail(listing_id: int) -> JSONResponse:
    """Detalhe de um anúncio (com fotos e breakdown do score) para o modal."""
    with session_scope() as session:
        item = session.get(Listing, listing_id)
        if item is None or item.status != "active":
            return JSONResponse({"error": "não encontrado"}, status_code=404)
        data = item.model_dump(mode="json")
        photos = _photos(item)
    data.pop("raw", None)
    data["photos"] = photos
    return JSONResponse(data)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Renderiza o shell; o grid (Tabulator) carrega os dados via /api/listings."""
    settings = get_settings()
    base_url = settings.site_base_url.rstrip("/")
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "destination": settings.destination_label,
            "neighborhoods": _all_neighborhoods(),
            "meta": {
                "title": settings.site_title,
                "description": settings.site_description,
                "url": base_url + "/",
                "image": base_url + "/static/og-image.png",
            },
        },
    )
