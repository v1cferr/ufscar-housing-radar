"""Coletor OLX (best-effort).

Estratégia: a OLX é um app Next.js que embute os dados da busca num
`<script id="__NEXT_DATA__">`. A lista orgânica fica em `props.pageProps.ads`
(usamos esse caminho; caímos para uma varredura recursiva só como fallback).

LIMITAÇÃO CONHECIDA (descoberta em 2026-06): nas nossas requisições a OLX
**ignora o filtro de região na URL** (provável geo-detecção por IP) e devolve
anúncios patrocinados de SP inteiro (São Caetano, Campinas, ...). Por isso
filtramos por `target_city` para nunca poluir o banco com imóveis de fora de
São Carlos. Enquanto o filtro de localização real da OLX não for resolvido,
este coletor tende a render pouco — use o coletor manual/CSV como base.

ATENÇÃO (ToS): o scraping da OLX é frágil e pode violar os termos de uso.
Use em baixo volume e para triagem pessoal (card V1C-68). Se quebrar, o
pipeline segue funcionando pelos outros coletores.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata

import httpx
from bs4 import BeautifulSoup

from housing_radar.collectors.base import Collector
from housing_radar.config import get_settings
from housing_radar.models import RawListing

logger = logging.getLogger(__name__)

# Mapeia nomes de "properties" da OLX para campos do RawListing.
_PROP_MAP = {
    "rooms": "bedrooms",
    "bedrooms": "bedrooms",
    "bathrooms": "bathrooms",
    "size": "area_m2",
    "garage_spaces": "parking_spots",
    "condominio": "condo_fee",
}


def _norm_city(value: str | None) -> str:
    if not value:
        return ""
    nfkd = unicodedata.normalize("NFKD", value).lower()
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def _looks_like_ad(obj: dict) -> bool:
    has_title = bool(obj.get("subject") or obj.get("title"))
    has_url = isinstance(obj.get("url"), str) and "olx.com.br" in obj.get("url", "")
    return has_title and has_url


def _walk(node, found: list[dict]) -> None:
    if isinstance(node, dict):
        if _looks_like_ad(node):
            found.append(node)
        for v in node.values():
            _walk(v, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)


def _extract_ads(data: dict) -> list[dict]:
    """Lista orgânica (props.pageProps.ads); fallback: varredura recursiva."""
    ads = data.get("props", {}).get("pageProps", {}).get("ads")
    if isinstance(ads, list) and ads:
        return ads
    found: list[dict] = []
    _walk(data, found)
    return found


def _parse_ad(ad: dict) -> RawListing:
    fields: dict = {}
    for prop in ad.get("properties", []) or []:
        if not isinstance(prop, dict):
            continue
        name = prop.get("name")
        value = prop.get("value") or prop.get("label")
        if name in _PROP_MAP and value:
            fields[_PROP_MAP[name]] = value

    location = ad.get("locationDetails") or {}
    neighborhood = location.get("neighbourhood") or location.get("neighborhood")
    city = location.get("municipality") or location.get("city")

    return RawListing(
        source="olx",
        source_id=str(ad.get("listId") or ad.get("adId") or ad.get("id") or ""),
        url=ad.get("url"),
        title=ad.get("subject") or ad.get("title"),
        # priceValue é a string canônica ("R$ 560.000"); normalize.py converte.
        price=ad.get("priceValue") or ad.get("price"),
        neighborhood=neighborhood,
        city=city,
        raw=ad,
        **fields,
    )


class OLXCollector(Collector):
    name = "olx"

    def __init__(
        self, search_url: str | None = None, target_city: str | None = "São Carlos"
    ) -> None:
        settings = get_settings()
        self.search_url = search_url or settings.olx_search_url
        self.timeout = settings.request_timeout
        self.user_agent = settings.user_agent
        # None desliga o filtro de cidade (aceita tudo que vier).
        self.target_city = _norm_city(target_city) if target_city else None

    def _fetch_page(self, client: httpx.Client, page: int) -> list[dict]:
        params = {"o": page} if page > 1 else None
        resp = client.get(self.search_url, params=params)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            logger.warning("OLX: __NEXT_DATA__ não encontrado (layout mudou?)")
            return []
        return _extract_ads(json.loads(tag.string))

    def _matches_city(self, ad: dict) -> bool:
        if self.target_city is None:
            return True
        city = (ad.get("locationDetails") or {}).get("municipality")
        return _norm_city(city) == self.target_city

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        settings = get_settings()
        pages = max_pages or settings.collect_max_pages
        headers = {"User-Agent": self.user_agent, "Accept-Language": "pt-BR,pt;q=0.9"}
        seen: set[str] = set()
        out: list[RawListing] = []
        skipped_city = 0

        with httpx.Client(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            for page in range(1, pages + 1):
                try:
                    ads = self._fetch_page(client, page)
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.warning("OLX: falha ao buscar página %s: %s", page, exc)
                    break
                if not ads:
                    break
                new = 0
                for ad in ads:
                    key = str(ad.get("listId") or ad.get("url") or "")
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    if not self._matches_city(ad):
                        skipped_city += 1
                        continue
                    out.append(_parse_ad(ad))
                    new += 1
                where = self.target_city or "qualquer cidade"
                logger.info("OLX: página %s -> %s anúncios em %s", page, new, where)
                if new == 0 and page > 1:
                    break

        if skipped_city:
            logger.warning(
                "OLX: %s anúncios descartados por não serem de %s "
                "(a OLX está ignorando o filtro de região na URL).",
                skipped_city,
                self.target_city,
            )
        return out
