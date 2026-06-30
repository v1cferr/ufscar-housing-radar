"""Coletor Chaves na Mão (chavesnamao.com.br) — baseado na URL do anúncio.

A listagem `/apartamentos-para-alugar/sp-sao-carlos/` é São Carlos-only e pagina
por `?pg=N` (~15 por página). Cada anúncio tem uma URL SEO riquíssima que codifica
tipo, transação, quartos, bairro, área, **preço** e id, ex.:

    /imovel/apartamento-para-alugar-2-quartos-com-garagem-sp-sao-carlos-morada-dos-deuses-62m2-RS1700/id-42440553/

Parseamos a própria URL (estável; independe da estrutura HTML, que é JS-rendered).

robots.txt: listagens públicas permitidas. UA de navegador e baixo volume (V1C-68).
"""

from __future__ import annotations

import logging
import re

import httpx

from housing_radar.collectors.base import Collector
from housing_radar.config import get_settings
from housing_radar.models import RawListing

logger = logging.getLogger(__name__)

_BASE = "https://www.chavesnamao.com.br"
# URL: /imovel/<tipo>-para-alugar-[N-quartos-][com-garagem-]sp-sao-carlos-
#      <bairro>[-<area>m2]-RS<preço>/id-<id>/
_LISTING_RE = re.compile(r"/imovel/[a-z0-9-]+-RS\d+/id-\d+/?")
_ID_RE = re.compile(r"/id-(\d+)")
_PRICE_RE = re.compile(r"-RS(\d+)/id-")
_AREA_RE = re.compile(r"-(\d+)m2-RS")
_ROOMS_RE = re.compile(r"-(\d+)-quartos?-")
_HOOD_RE = re.compile(r"sp-sao-carlos-(.+?)-(?:\d+m2-)?RS\d+")


def _deslug(slug: str) -> str:
    return " ".join(w for w in slug.split("-") if w).title() or None


def parse_listing_url(url: str, tipo_imovel: str = "apartamento") -> RawListing | None:
    """Extrai os campos da própria URL do anúncio. None se faltar id ou preço."""
    m_id, m_price = _ID_RE.search(url), _PRICE_RE.search(url)
    if not m_id or not m_price:
        return None
    m_area, m_rooms, m_hood = _AREA_RE.search(url), _ROOMS_RE.search(url), _HOOD_RE.search(url)
    return RawListing(
        source="chavesnamao",
        source_id=m_id.group(1),
        url=url if url.startswith("http") else f"{_BASE}{url}",
        rent_price=float(m_price.group(1)),
        transacao="aluguel",
        tipo_imovel=tipo_imovel,
        area_m2=float(m_area.group(1)) if m_area else None,
        bedrooms=int(m_rooms.group(1)) if m_rooms else None,
        neighborhood=_deslug(m_hood.group(1)) if m_hood else None,
        city="São Carlos",
    )


class ChavesNaMaoCollector(Collector):
    name = "chavesnamao"

    def __init__(
        self,
        search_path: str = "/apartamentos-para-alugar/sp-sao-carlos/",
        tipo_imovel: str = "apartamento",
        cap_pages: int = 25,
    ) -> None:
        settings = get_settings()
        self.search_url = f"{_BASE}{search_path}"
        self.tipo_imovel = tipo_imovel
        self.cap_pages = cap_pages
        self.timeout = settings.request_timeout
        self.user_agent = settings.user_agent

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        pages = max_pages or self.cap_pages
        headers = {"User-Agent": self.user_agent, "Accept-Language": "pt-BR,pt;q=0.9"}
        seen: set[str] = set()
        out: list[RawListing] = []
        with httpx.Client(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            for pg in range(1, pages + 1):
                url = self.search_url if pg == 1 else f"{self.search_url}?pg={pg}"
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.warning("ChavesNaMao: falha na página %s: %s", pg, exc)
                    break
                page_urls = set(_LISTING_RE.findall(resp.text))
                new = [u for u in page_urls if u not in seen]
                if not new:  # paginação esgotou (ou repetiu)
                    break
                for u in new:
                    seen.add(u)
                    rl = parse_listing_url(u, self.tipo_imovel)
                    if rl:
                        out.append(rl)
                logger.info("ChavesNaMao: pág %s -> %s novos (total %s)", pg, len(new), len(out))
        logger.info("ChavesNaMao: %s anúncios coletados", len(out))
        return out
