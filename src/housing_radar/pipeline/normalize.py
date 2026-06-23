"""Normalização: RawListing (frouxo) -> Listing (tipado e limpo)."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from housing_radar.models import Listing, RawListing

_NUM_RE = re.compile(r"[\d.,]+")


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def slugify(text: str | None) -> str:
    if not text:
        return ""
    text = _strip_accents(text).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def parse_money(value: float | str | None) -> float | None:
    """'R$ 350.000,00' -> 350000.0 ; '1.200' -> 1200.0 ; 350000 -> 350000.0"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM_RE.search(value)
    if not match:
        return None
    raw = match.group(0)
    # Formato pt-BR: '.' é milhar e ',' é decimal.
    raw = raw.replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_float(value: float | str | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM_RE.search(value.replace(".", "").replace(",", "."))
    try:
        return float(match.group(0)) if match else None
    except (ValueError, AttributeError):
        return None


def parse_int(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def build_address(raw: RawListing) -> str | None:
    parts = [raw.address, raw.neighborhood, raw.city]
    parts = [p for p in parts if p]
    return ", ".join(dict.fromkeys(parts)) if parts else None


def make_dedupe_key(raw: RawListing, price: float | None, area: float | None) -> str:
    """Chave estável: fonte+id quando houver; senão hash de atributos."""
    if raw.source_id:
        return f"{raw.source}:{raw.source_id}"
    basis = "|".join(
        str(x)
        for x in (slugify(raw.title), slugify(raw.neighborhood), price, area)
    )
    digest = hashlib.sha1(basis.encode()).hexdigest()[:12]
    return f"{raw.source}:h:{digest}"


def normalize(raw: RawListing) -> Listing:
    price = parse_money(raw.price)
    condo_fee = parse_money(raw.condo_fee)
    area = parse_float(raw.area_m2)
    lat = parse_float(raw.lat)
    lon = parse_float(raw.lon)

    return Listing(
        source=raw.source,
        source_id=raw.source_id,
        url=raw.url,
        dedupe_key=make_dedupe_key(raw, price, area),
        title=raw.title.strip() if raw.title else None,
        price=price,
        condo_fee=condo_fee,
        area_m2=area,
        bedrooms=parse_int(raw.bedrooms),
        bathrooms=parse_int(raw.bathrooms),
        parking_spots=parse_int(raw.parking_spots),
        address=build_address(raw),
        neighborhood=raw.neighborhood,
        city=raw.city,
        lat=lat,
        lon=lon,
        geocoded=lat is not None and lon is not None,
        description=raw.description,
        raw=raw.raw,
    )
