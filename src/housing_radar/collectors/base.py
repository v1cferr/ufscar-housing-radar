"""Interface comum dos coletores de anúncios."""

from __future__ import annotations

from abc import ABC, abstractmethod

from housing_radar.models import RawListing


class Collector(ABC):
    """Cada fonte (OLX, ZAP, manual, ...) implementa esta interface.

    O coletor é responsável apenas por *buscar* e devolver `RawListing`s.
    A limpeza, dedup, geocoding e score ficam no pipeline.
    """

    #: nome curto e estável, usado em CLI e no campo Listing.source
    name: str = "base"

    @abstractmethod
    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        """Retorna a lista de anúncios brutos encontrados."""
        raise NotImplementedError
