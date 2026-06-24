"""Orquestração do pipeline: coletar -> normalizar -> dedup -> persistir -> enriquecer."""

from __future__ import annotations

import logging
import time

from sqlmodel import select

from housing_radar.collectors.base import Collector
from housing_radar.db import session_scope
from housing_radar.models import Listing
from housing_radar.pipeline.dedupe import dedupe
from housing_radar.pipeline.geocode import Geocoder
from housing_radar.pipeline.normalize import normalize
from housing_radar.pipeline.score import ScoreConfig, score_listing
from housing_radar.pipeline.travel import TravelCalculator

logger = logging.getLogger(__name__)

# Campos atualizáveis quando um anúncio já existente é recoletado.
_MUTABLE = ("title", "price", "condo_fee", "area_m2", "bedrooms", "bathrooms",
            "parking_spots", "url", "description", "neighborhood", "address")


def collect_listings(collector: Collector, max_pages: int | None = None) -> list[Listing]:
    """Coleta, normaliza e deduplica — sem tocar no banco."""
    raws = collector.collect(max_pages=max_pages)
    listings = [normalize(r) for r in raws]
    deduped = dedupe(listings)
    logger.info("%s: %s brutos -> %s após dedup", collector.name, len(raws), len(deduped))
    return deduped


def ingest(collector: Collector, max_pages: int | None = None) -> dict[str, int]:
    """Coleta e faz upsert no banco por dedupe_key."""
    listings = collect_listings(collector, max_pages=max_pages)
    inserted = updated = 0
    with session_scope() as session:
        for item in listings:
            existing = session.exec(
                select(Listing).where(Listing.dedupe_key == item.dedupe_key)
            ).first()
            if existing is None:
                session.add(item)
                inserted += 1
            else:
                changed = False
                for fieldname in _MUTABLE:
                    new = getattr(item, fieldname)
                    if new is not None and new != getattr(existing, fieldname):
                        setattr(existing, fieldname, new)
                        changed = True
                if changed:
                    from datetime import UTC, datetime

                    existing.updated_at = datetime.now(UTC)
                    # Preço/atributos mudaram: zera o score para recálculo.
                    existing.score = None
                    session.add(existing)
                    updated += 1
    return {"collected": len(listings), "inserted": inserted, "updated": updated}


def enrich(*, limit: int | None = None, regeocode: bool = False) -> dict[str, int]:
    """Geocoda os que faltam, calcula tempo até a UFSCar e (re)calcula o score."""
    geocoder = Geocoder()
    travel = TravelCalculator()
    cfg = ScoreConfig()
    stats = {"geocoded": 0, "travel": 0, "scored": 0}

    with session_scope() as session:
        listings = session.exec(select(Listing).where(Listing.status == "active")).all()
        if limit:
            listings = listings[:limit]

        for listing in listings:
            if (regeocode or not listing.geocoded) and listing.address:
                coords = geocoder.geocode(listing.address)
                if coords:
                    listing.lat, listing.lon = coords
                    listing.geocoded = True
                    stats["geocoded"] += 1

            if listing.lat is not None and listing.lon is not None:
                result = travel.compute(listing.lat, listing.lon)
                if result:
                    listing.dist_ufscar_km = result.dist_km
                    listing.time_walk_min = result.time_walk_min
                    listing.time_bike_min = result.time_bike_min
                    listing.time_car_min = result.time_car_min
                    listing.travel_provider = result.provider
                    stats["travel"] += 1

            score, breakdown = score_listing(listing, cfg)
            listing.score = score
            listing.score_breakdown = breakdown
            if score is not None:
                stats["scored"] += 1
            session.add(listing)

    return stats


def rescore_all() -> dict[str, int]:
    """Recalcula APENAS o score (não toca em geocode/travel — preserva o refino ORS)."""
    cfg = ScoreConfig()
    n = 0
    with session_scope() as session:
        for listing in session.exec(select(Listing).where(Listing.status == "active")).all():
            listing.score, listing.score_breakdown = score_listing(listing, cfg)
            session.add(listing)
            n += 1
    return {"rescored": n}


def backfill_rent_from_raw() -> dict[str, int]:
    """Preenche rent_price (a partir de `valLocation` no dado cru) para registros antigos.

    Migração idempotente: só toca em quem ainda não tem rent_price. Não recalcula o
    score — chame rescore_all() em seguida.
    """
    from housing_radar.pipeline.normalize import parse_money

    filled = 0
    with session_scope() as session:
        for listing in session.exec(select(Listing)).all():
            if listing.rent_price is None and listing.raw:
                rent = parse_money(listing.raw.get("valLocation"))
                if rent and rent > 0:
                    listing.rent_price = rent
                    session.add(listing)
                    filled += 1
    return {"rent_filled": filled}


def run_all(collector: Collector, max_pages: int | None = None) -> dict[str, int]:
    ingest_stats = ingest(collector, max_pages=max_pages)
    enrich_stats = enrich()
    return {**ingest_stats, **enrich_stats}


def refine_routes_ors(*, limit: int = 40, delay: float = 5.0) -> dict:
    """Refina o deslocamento dos top-N por score com ORS (real, por modal) e re-scoreia.

    Direcionado de propósito: a cota grátis do ORS (~2000/dia, ~40/min) não comporta
    o acervo inteiro (3 chamadas por imóvel). O `delay` mantém a taxa abaixo do limite.
    """
    travel = TravelCalculator(use_ors=True)
    if not travel.ors_enabled:
        return {
            "erro": "ORS indisponível: faltou HR_ORS_API_KEY ou o pacote (uv sync --extra ors)."
        }

    cfg = ScoreConfig()
    refined = 0
    with session_scope() as session:
        candidates = session.exec(
            select(Listing).where(Listing.status == "active", Listing.lat.is_not(None))
        ).all()
        candidates = sorted(candidates, key=lambda x: (x.score is None, -(x.score or 0)))[:limit]
        for i, listing in enumerate(candidates):
            result = travel.compute(listing.lat, listing.lon)
            if result and result.provider == "ors":
                listing.dist_ufscar_km = result.dist_km
                listing.time_walk_min = result.time_walk_min
                listing.time_bike_min = result.time_bike_min
                listing.time_car_min = result.time_car_min
                listing.travel_provider = "ors"
                listing.score, listing.score_breakdown = score_listing(listing, cfg)
                session.add(listing)
                refined += 1
            if delay and i < len(candidates) - 1:
                time.sleep(delay)
    return {"refinados_ors": refined, "de": len(candidates)}
