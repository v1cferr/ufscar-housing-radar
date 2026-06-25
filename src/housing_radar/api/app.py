"""Aplicação FastAPI: API JSON + dashboard HTML dos apartamentos ranqueados."""

from __future__ import annotations

import json
import re
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlmodel import select

from housing_radar.config import get_settings
from housing_radar.db import init_db, session_scope
from housing_radar.models import Listing, Setting

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


# --- Renderização legível (server-side, p/ humanos sem JS e p/ IAs) ----------
def _render_row(item: Listing, with_photo: bool = True) -> dict:
    """Campos achatados de um anúncio + foto principal, para templates/JSON-LD."""
    photo = None
    if with_photo:
        photos = _photos(item)
        photo = photos[0] if photos else None
    desc = (item.description or "").strip()
    return {
        "id": item.id,
        "title": item.title,
        "price": item.price,
        "rent_price": item.rent_price,
        "condo_fee": item.condo_fee,
        "area_m2": item.area_m2,
        "bedrooms": item.bedrooms,
        "bathrooms": item.bathrooms,
        "parking_spots": item.parking_spots,
        "neighborhood": item.neighborhood,
        "city": item.city,
        "dist_ufscar_km": item.dist_ufscar_km,
        "time_walk_min": item.time_walk_min,
        "time_car_min": item.time_car_min,
        "score": item.score,
        "url": item.url,
        "source": item.source,
        "photo": photo,
        "description": desc[:400] + ("…" if len(desc) > 400 else ""),
    }


def _featured(limit: int, **filters) -> list[dict]:
    """Top-N por score (já deduplicado entre fontes), como dicts prontos p/ render."""
    listings = _query_listings(sort="score", **filters)
    reps, _ = _collapse_duplicates(listings, _SORTS["score"])
    return [_render_row(i) for i in reps[:limit]]


def _listings_ld(rows: list[dict], base_url: str) -> str:
    """JSON-LD schema.org (ItemList de Apartment/Offer) para leitura por máquinas."""
    elements = []
    for pos, r in enumerate(rows, 1):
        item: dict = {"@type": "Apartment", "name": r["title"] or "Apartamento"}
        item["url"] = r["url"] or f"{base_url}/lista"
        if r["photo"]:
            item["image"] = r["photo"]
        if r["area_m2"]:
            item["floorSize"] = {"@type": "QuantitativeValue", "value": r["area_m2"], "unitCode": "MTK"}
        if r["bedrooms"] is not None:
            item["numberOfRoomsTotal"] = r["bedrooms"]
        if r["neighborhood"]:
            item["address"] = {
                "@type": "PostalAddress",
                "streetAddress": r["neighborhood"],
                "addressLocality": r["city"] or "São Carlos",
                "addressRegion": "SP",
                "addressCountry": "BR",
            }
        if r["price"]:
            item["offers"] = {
                "@type": "Offer",
                "price": int(round(r["price"])),
                "priceCurrency": "BRL",
                "availability": "https://schema.org/InStock",
            }
        elements.append({"@type": "ListItem", "position": pos, "item": item})
    doc = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": "Apartamentos à venda perto da UFSCar São Carlos",
        "numberOfItems": len(rows),
        "itemListElement": elements,
    }
    return json.dumps(doc, ensure_ascii=False)


def _duplicates_of(listing: Listing) -> list[Listing]:
    """Todos os anúncios do mesmo imóvel (mesma assinatura de dedup), incl. ele."""
    sig = _dup_signature(listing)
    if sig[0] == "uniq":
        return [listing]
    with session_scope() as session:
        stmt = select(Listing).where(Listing.status == "active")
        if listing.bedrooms is not None:
            stmt = stmt.where(Listing.bedrooms == listing.bedrooms)
        candidates = session.exec(stmt).all()
    members = [c for c in candidates if _dup_signature(c) == sig]
    return members or [listing]


def _detail_for_render(listing_id: int) -> dict | None:
    """Detalhe completo de um imóvel p/ a página /imovel/{id} (fotos + fontes)."""
    with session_scope() as session:
        item = session.get(Listing, listing_id)
        if item is None or item.status != "active":
            return None
    row = _render_row(item, with_photo=False)
    photos = _photos(item)
    row.update(
        {
            "photos": photos,
            "photo": photos[0] if photos else None,
            "description": (item.description or "").strip(),  # completa, sem cortar
            "lat": item.lat,
            "lon": item.lon,
            "time_bike_min": item.time_bike_min,
            "travel_provider": item.travel_provider,
            "score_breakdown": item.score_breakdown,
        }
    )
    # Todas as fontes onde o imóvel aparece (links diretos p/ cada anúncio).
    sources: list[dict] = []
    seen: set[str] = set()
    for member in _duplicates_of(item):
        key = member.url or f"{member.source}:{member.id}"
        if key in seen:
            continue
        seen.add(key)
        sources.append({"source": member.source, "url": member.url})
    row["sources"] = sources
    return row


def _listing_ld(d: dict, base_url: str) -> str:
    """JSON-LD schema.org de um único imóvel (Apartment + Offer + geo)."""
    item: dict = {
        "@context": "https://schema.org",
        "@type": "Apartment",
        "@id": f"{base_url}/imovel/{d['id']}",
        "url": f"{base_url}/imovel/{d['id']}",
        "name": d["title"] or "Apartamento",
    }
    if d["photos"]:
        item["image"] = d["photos"][:8]
    if d.get("description"):
        item["description"] = d["description"][:500]
    if d["area_m2"]:
        item["floorSize"] = {"@type": "QuantitativeValue", "value": d["area_m2"], "unitCode": "MTK"}
    if d["bedrooms"] is not None:
        item["numberOfRoomsTotal"] = d["bedrooms"]
    if d["bathrooms"] is not None:
        item["numberOfBathroomsTotal"] = d["bathrooms"]
    if d["neighborhood"]:
        item["address"] = {
            "@type": "PostalAddress",
            "streetAddress": d["neighborhood"],
            "addressLocality": d["city"] or "São Carlos",
            "addressRegion": "SP",
            "addressCountry": "BR",
        }
    if d.get("lat") and d.get("lon"):
        item["geo"] = {"@type": "GeoCoordinates", "latitude": d["lat"], "longitude": d["lon"]}
    if d["price"]:
        offer = {
            "@type": "Offer",
            "price": int(round(d["price"])),
            "priceCurrency": "BRL",
            "availability": "https://schema.org/InStock",
        }
        if d["sources"] and d["sources"][0]["url"]:
            offer["url"] = d["sources"][0]["url"]
        item["offers"] = offer
    return json.dumps(item, ensure_ascii=False)


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


# Config compartilhada (financiamento + mudança). Defaults preenchem o que faltar.
_DEFAULT_SETTINGS = {
    "parcela_max": "",      # filtro: parcela mensal máxima (R$)
    "entrada_pct": "20",    # entrada (% do valor) p/ estimar o financiamento
    "juros_aa": "11",       # juros (% ao ano)
    "prazo_meses": "360",   # prazo do financiamento (meses)
    "origin": "",           # endereço de origem (ou só o CEP) p/ estimar a mudança
    "origin_lat": "",       # lat/lon geocodados da origem (preenchidos no save)
    "origin_lon": "",
    "origin_resolved": "",  # endereço completo resolvido a partir do CEP (confirmação)
}


@app.get("/api/settings")
def api_get_settings() -> JSONResponse:
    """Config compartilhada (com defaults preenchidos)."""
    with session_scope() as session:
        rows = session.exec(select(Setting)).all()
    out = dict(_DEFAULT_SETTINGS)
    out.update({r.key: r.value for r in rows})
    return JSONResponse(out)


@app.get("/api/rates")
def api_rates() -> JSONResponse:
    """Selic + taxa do financiamento imobiliário (Banco Central) p/ os juros.

    Best-effort: retorna {available: false} se o BCB estiver fora do ar.
    """
    from housing_radar.pipeline.refs import fetch_rates

    rates = fetch_rates()
    if not rates:
        return JSONResponse({"available": False})
    return JSONResponse({"available": True, **rates})


@app.post("/api/settings")
def api_set_settings(data: dict = Body(...)) -> JSONResponse:
    """Salva (upsert) as chaves enviadas — compartilhado, sem login (uso pessoal).

    Se 'origin' mudar, geocoda e grava origin_lat/origin_lon (p/ estimar a mudança).
    """
    # Geocoda a origem fora da sessão (Nominatim ~1s) e injeta lat/lon.
    # Se a origem for um CEP, resolve via ViaCEP primeiro (funciona sem número).
    if "origin" in data:
        origin = (data.get("origin") or "").strip()
        if origin:
            from housing_radar.pipeline.geocode import Geocoder
            from housing_radar.pipeline.refs import resolve_cep

            cep = resolve_cep(origin)
            if cep:
                data["origin_resolved"] = cep["address"]
                # tenta o endereço completo; se o logradouro não casar no Nominatim,
                # cai para bairro + cidade (sempre geocoda bem, e basta p/ a estimativa).
                coarse = ", ".join(
                    p for p in (cep["bairro"], cep["localidade"], cep["uf"]) if p
                )
                candidates = [cep["address"]]
                if coarse and coarse != cep["address"]:
                    candidates.append(coarse)
            else:
                data["origin_resolved"] = ""
                candidates = [origin]
            geocoder = Geocoder()
            coords = None
            for cand in candidates:
                coords = geocoder.geocode(cand)
                if coords:
                    break
            data["origin_lat"] = str(coords[0]) if coords else ""
            data["origin_lon"] = str(coords[1]) if coords else ""
        else:
            data["origin_lat"] = ""
            data["origin_lon"] = ""
            data["origin_resolved"] = ""

    with session_scope() as session:
        for key, value in data.items():
            row = session.get(Setting, key)
            if row is None:
                session.add(Setting(key=key, value=str(value)))
            else:
                row.value = str(value)
                session.add(row)
    return JSONResponse({"ok": True})


@app.post("/api/listings/{listing_id}/favorite")
def api_set_favorite(listing_id: int, value: bool = Body(..., embed=True)) -> JSONResponse:
    """Marca/desmarca favorito (compartilhado — sem login, uso pessoal V1C-68)."""
    with session_scope() as session:
        item = session.get(Listing, listing_id)
        if item is None:
            return JSONResponse({"error": "não encontrado"}, status_code=404)
        item.favorite = value
        session.add(item)
    return JSONResponse({"id": listing_id, "favorite": value})


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    """Renderiza o shell; o grid (Tabulator) carrega os dados via /api/listings.

    Para leitores sem JS (IAs que abrem o link, crawlers), embute os top imóveis
    em <noscript> + JSON-LD schema.org — assim o conteúdo é legível sem executar JS.
    """
    settings = get_settings()
    base_url = settings.site_base_url.rstrip("/")
    featured = _featured(limit=30)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "destination": settings.destination_label,
            "ufscar_lat": settings.ufscar_lat,
            "ufscar_lon": settings.ufscar_lon,
            "neighborhoods": _all_neighborhoods(),
            "featured": featured,
            "ld_json": _listings_ld(featured, base_url),
            "meta": {
                "title": settings.site_title,
                "description": settings.site_description,
                "url": base_url + "/",
                "image": base_url + "/static/og-image.png",
            },
        },
    )


@app.get("/lista", response_class=HTMLResponse)
def lista(
    request: Request,
    q: str | None = Query(default=None),
    max_price: str | None = Query(default=None),
    min_bedrooms: str | None = Query(default=None),
    min_area: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> HTMLResponse:
    """Lista server-rendered (sem JS) — pensada para IAs e leitura direta.

    Filtros por querystring: ?q=&max_price=&min_bedrooms=&min_area=&limit=.
    """
    settings = get_settings()
    base_url = settings.site_base_url.rstrip("/")
    rows = _featured(
        limit=limit,
        q=q,
        max_price=_to_float(max_price),
        min_bedrooms=_to_int(min_bedrooms),
        min_area=_to_float(min_area),
    )
    return templates.TemplateResponse(
        request,
        "lista.html",
        {
            "rows": rows,
            "total": len(rows),
            "destination": settings.destination_label,
            "ld_json": _listings_ld(rows, base_url),
            "filters": {
                "q": q or "",
                "max_price": max_price or "",
                "min_bedrooms": min_bedrooms or "",
                "min_area": min_area or "",
                "limit": limit,
            },
            "meta": {
                "title": "Lista de apartamentos — " + settings.site_title,
                "description": settings.site_description,
                "url": base_url + "/lista",
                "image": base_url + "/static/og-image.png",
            },
        },
    )


@app.get("/imovel/{listing_id}", response_class=HTMLResponse)
def imovel(request: Request, listing_id: int) -> HTMLResponse:
    """Página própria de um imóvel (server-rendered) — URL citável, com fotos,

    dados completos, links diretos para todas as fontes e JSON-LD schema.org.
    """
    d = _detail_for_render(listing_id)
    settings = get_settings()
    base_url = settings.site_base_url.rstrip("/")
    if d is None:
        return templates.TemplateResponse(
            request,
            "imovel.html",
            {"row": None, "meta": {"title": "Imóvel não encontrado", "description": "",
             "url": f"{base_url}/imovel/{listing_id}", "image": base_url + "/static/og-image.png"},
             "ld_json": "", "destination": settings.destination_label},
            status_code=404,
        )
    facts = []
    if d["bedrooms"] is not None:
        facts.append(f"{d['bedrooms']} quartos")
    if d["area_m2"]:
        facts.append(f"{int(round(d['area_m2']))} m²")
    if d["neighborhood"]:
        facts.append(d["neighborhood"])
    if d["price"]:
        facts.append("R$ " + _int_br(d["price"]))
    desc = " · ".join(facts) or settings.site_description
    return templates.TemplateResponse(
        request,
        "imovel.html",
        {
            "row": d,
            "destination": settings.destination_label,
            "ld_json": _listing_ld(d, base_url),
            "meta": {
                "title": (d["title"] or "Apartamento") + " — " + settings.site_title,
                "description": desc,
                "url": f"{base_url}/imovel/{listing_id}",
                "image": d["photo"] or (base_url + "/static/og-image.png"),
            },
        },
    )


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms_txt() -> PlainTextResponse:
    """Guia em markdown para ferramentas de IA (convenção llmstxt.org)."""
    base = get_settings().site_base_url.rstrip("/")
    body = f"""# UFSCar Housing Radar

> Apartamentos à venda perto da UFSCar (São Carlos/SP), coletados de várias
> imobiliárias e portais, geolocalizados e ranqueados por um score 0-100
> (proximidade, preço, área, condomínio, quartos, vagas, custo-benefício).

O site principal ({base}/) é um app JavaScript; para ler os dados sem executar JS,
use os recursos abaixo.

## Dados legíveis por máquina

- [Lista completa em JSON]({base}/api/listings): array de imóveis. Cada item tem
  id, title, price (R$), rent_price, condo_fee, area_m2, bedrooms, bathrooms,
  parking_spots, neighborhood, city, lat, lon, dist_ufscar_km, time_walk_min,
  time_bike_min, time_car_min, score (0-100), url (anúncio na fonte), source,
  dup_count, dup_sources.
- [Detalhe de um imóvel em JSON]({base}/api/listings/ID): inclui o campo `photos`
  (URLs das fotos em alta) e `score_breakdown`.
- [Página de um imóvel em HTML]({base}/imovel/ID): URL citável de um único
  apartamento, com fotos, dados completos, descrição e links diretos para
  todas as fontes onde ele aparece (use o `id` de /api/listings).
- [Lista em HTML server-rendered]({base}/lista): mesma informação com fotos,
  descrições e preços já renderizados (boa para leitura direta).

## Filtros (querystring, valem para /api/listings e /lista)

- `q`: texto livre (título, bairro, fonte) ou ID/URL de um anúncio colado.
- `max_price`: preço máximo em R$.
- `min_bedrooms`: número mínimo de quartos.
- `min_area`: área mínima em m².
- `max_car_min`: tempo máximo de carro até a UFSCar (min) — só em /api/listings.
- `sort`: score | price | car | area (padrão: score) — só em /api/listings.
- `limit`: máximo de itens.

Exemplos:
- {base}/lista?max_price=300000&min_bedrooms=2
- {base}/api/listings?q=Santa+Felicia&sort=price&limit=20

## Observações

- Preços em reais (R$). Tempos até a UFSCar em minutos. Distância em km.
- "score" é uma nota 0-100 calculada pelo projeto, não um valor de mercado.
- Os dados são best-effort, para uso pessoal; confira sempre no anúncio original (url).
"""
    return PlainTextResponse(body, media_type="text/markdown; charset=utf-8")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> PlainTextResponse:
    base = get_settings().site_base_url.rstrip("/")
    body = f"User-agent: *\nAllow: /\n\n# Guia para IAs: {base}/llms.txt\nSitemap: {base}/sitemap.xml\n"
    return PlainTextResponse(body)


@app.get("/sitemap.xml", response_class=PlainTextResponse)
def sitemap_xml() -> PlainTextResponse:
    base = get_settings().site_base_url.rstrip("/")
    urls = [f"{base}/", f"{base}/lista", f"{base}/llms.txt"]
    with session_scope() as session:
        ids = session.exec(
            select(Listing.id).where(Listing.status == "active").order_by(Listing.score.desc())
        ).all()
    urls += [f"{base}/imovel/{i}" for i in ids]
    items = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'
    return PlainTextResponse(xml, media_type="application/xml")
