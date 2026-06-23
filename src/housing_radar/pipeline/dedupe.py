"""Remoção de duplicados entre listas de Listing recém-normalizados.

Dois níveis:
1. `dedupe_key` (fonte+id) — duplicata exata da mesma fonte.
2. assinatura de atributos (bairro + preço + área + quartos) — mesmo imóvel
   anunciado em fontes/anúncios diferentes.
"""

from __future__ import annotations

from housing_radar.models import Listing
from housing_radar.pipeline.normalize import slugify


def _signature(listing: Listing) -> tuple:
    return (
        slugify(listing.neighborhood),
        round(listing.price) if listing.price else None,
        round(listing.area_m2) if listing.area_m2 else None,
        listing.bedrooms,
    )


def dedupe(listings: list[Listing]) -> list[Listing]:
    by_key: dict[str, Listing] = {}
    for item in listings:
        # Mantém o primeiro de cada dedupe_key.
        by_key.setdefault(item.dedupe_key, item)

    seen_sig: set[tuple] = set()
    out: list[Listing] = []
    for item in by_key.values():
        sig = _signature(item)
        # Só usa a assinatura quando ela é informativa (tem preço e área).
        if sig[1] is not None and sig[2] is not None:
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
        out.append(item)
    return out
