"""Coletor manual: lê anúncios de um CSV.

Fonte mais confiável para começar (ToS-safe). Útil para:
- semear o banco com imóveis que você ou sua mãe já encontraram;
- importar exportações de planilhas;
- testar o pipeline ponta a ponta sem depender de scraping.

Colunas esperadas (todas opcionais exceto `title` ou `address`):
    source_id, url, title, price, condo_fee, area_m2, bedrooms, bathrooms,
    parking_spots, address, neighborhood, city, lat, lon, description
"""

from __future__ import annotations

import csv
from pathlib import Path

from housing_radar.collectors.base import Collector
from housing_radar.models import RawListing

_FIELDS = {
    "source_id", "url", "title", "price", "condo_fee", "area_m2", "bedrooms",
    "bathrooms", "parking_spots", "address", "neighborhood", "city", "lat",
    "lon", "description",
}


class ManualCSVCollector(Collector):
    name = "manual"

    def __init__(self, csv_path: str | Path) -> None:
        self.csv_path = Path(csv_path)

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"CSV não encontrado: {self.csv_path}")

        out: list[RawListing] = []
        with self.csv_path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for i, row in enumerate(reader):
                data = {
                    k: (v.strip() if isinstance(v, str) else v)
                    for k, v in row.items()
                    if k in _FIELDS and v not in (None, "")
                }
                if not data.get("title") and not data.get("address"):
                    continue
                data.setdefault("source_id", str(i))
                out.append(RawListing(source=self.name, **data))
        return out
