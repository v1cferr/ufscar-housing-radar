"""Exportação dos anúncios ranqueados para planilha (CSV e XLSX)."""

from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from sqlmodel import select

from housing_radar.config import get_settings
from housing_radar.db import session_scope
from housing_radar.models import Listing

# Ordem das colunas na planilha (rótulo legível -> atributo).
COLUMNS: list[tuple[str, str]] = [
    ("Score", "score"),
    ("Título", "title"),
    ("Bairro", "neighborhood"),
    ("Preço (R$)", "price"),
    ("Condomínio (R$)", "condo_fee"),
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
    rows = _ranked_listings()
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([label for label, _ in COLUMNS])
        for item in rows:
            writer.writerow([getattr(item, attr) for _, attr in COLUMNS])
    return path


def export_xlsx(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _ranked_listings()
    wb = Workbook()
    ws = wb.active
    ws.title = "Apartamentos UFSCar"
    ws.append([label for label, _ in COLUMNS])
    for item in rows:
        ws.append([getattr(item, attr) for _, attr in COLUMNS])
    # Congela o cabeçalho.
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def export(fmt: str = "xlsx", path: str | Path | None = None) -> Path:
    settings = get_settings()
    if path is None:
        path = Path(settings.export_dir) / f"apartamentos_ufscar.{fmt}"
    if fmt == "csv":
        return export_csv(path)
    if fmt == "xlsx":
        return export_xlsx(path)
    raise ValueError(f"Formato não suportado: {fmt}")
