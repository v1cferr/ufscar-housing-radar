"""Exportação dos anúncios ranqueados para planilha (CSV e XLSX)."""

from __future__ import annotations

import csv
from collections.abc import Callable
from pathlib import Path

from openpyxl import Workbook
from sqlmodel import select

from housing_radar.config import get_settings
from housing_radar.db import session_scope
from housing_radar.models import Listing, Setting
from housing_radar.pipeline.cost import CostConfig, cost_config_from_settings, monthly_cost

# Resolver de coluna: nome de atributo (str) OU callable(listing) -> valor.
Resolver = str | Callable[[Listing], object]


def _columns(cfg: CostConfig) -> list[tuple[str, Resolver]]:
    """Colunas da planilha (rótulo -> atributo/cálculo). O custo mensal depende
    dos params de financiamento (cfg), por isso é montado por chamada."""
    return [
        ("Score", "score"),
        ("Título", "title"),
        ("Transação", "transacao"),
        ("Tipo", "tipo_imovel"),
        ("Estratégia", "estrategia"),
        ("Bairro", "neighborhood"),
        ("Preço (R$)", "price"),
        ("Aluguel (R$/mês)", "rent_price"),
        ("Condomínio (R$)", "condo_fee"),
        ("Custo mensal est. (R$)", lambda item: monthly_cost(item, cfg)[0]),
        ("Área (m²)", "area_m2"),
        ("Quartos", "bedrooms"),
        ("Banheiros", "bathrooms"),
        ("Vagas", "parking_spots"),
        ("Dist. UFSCar (km)", "dist_ufscar_km"),
        ("A pé (min)", "time_walk_min"),
        ("Bici (min)", "time_bike_min"),
        ("Carro (min)", "time_car_min"),
        ("Fonte", "source"),
        ("URL", "url"),
    ]


def _value(item: Listing, resolver: Resolver) -> object:
    return resolver(item) if callable(resolver) else getattr(item, resolver)


def _load_cost_config() -> CostConfig:
    """Monta a CostConfig a partir das settings salvas (financiamento)."""
    with session_scope() as session:
        rows = session.exec(select(Setting)).all()
    return cost_config_from_settings({r.key: r.value for r in rows})


def _ranked_listings() -> list[Listing]:
    with session_scope() as session:
        listings = session.exec(
            select(Listing).where(Listing.status == "active")
        ).all()
    # None de score vai pro fim.
    return sorted(listings, key=lambda x: (x.score is None, -(x.score or 0)))


def export_csv(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = _columns(_load_cost_config())
    rows = _ranked_listings()
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for label, _ in columns])
        for item in rows:
            writer.writerow([_value(item, resolver) for _, resolver in columns])
    return path


def export_xlsx(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = _columns(_load_cost_config())
    rows = _ranked_listings()
    wb = Workbook()
    ws = wb.active
    ws.title = "Moradia UFSCar"
    ws.append([label for label, _ in columns])
    for item in rows:
        ws.append([_value(item, resolver) for _, resolver in columns])
    # Congela o cabeçalho.
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def export(fmt: str = "xlsx", path: str | Path | None = None) -> Path:
    settings = get_settings()
    if path is None:
        path = Path(settings.export_dir) / f"moradia_ufscar.{fmt}"
    if fmt == "csv":
        return export_csv(path)
    if fmt == "xlsx":
        return export_xlsx(path)
    raise ValueError(f"Formato não suportado: {fmt}")
