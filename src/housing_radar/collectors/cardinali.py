"""Coletor Cardinali Imóveis (cardinali.com.br) — HTML server-rendered.

Plataforma própria (Bootstrap); página em **ISO-8859-1** (precisa decodificar).
Os ~27 cards por página vêm no HTML inicial, sem JS. Paginação via `?pag=N`.
A listagem `/comprar/Sao-Carlos/Apartamento` já é São Carlos-only.

robots.txt: `/comprar/` é permitido (bloqueia só bots de IA/SEO). Usamos um
User-Agent de navegador e baixo volume (card V1C-68).
"""

from __future__ import annotations

import logging
import re

import httpx
from bs4 import BeautifulSoup

from housing_radar.collectors.base import Collector
from housing_radar.config import get_settings
from housing_radar.models import RawListing

logger = logging.getLogger(__name__)

_BASE = "https://www.cardinali.com.br"
_SEARCH = f"{_BASE}/comprar/Sao-Carlos/Apartamento"


def _num(pattern: str, text: str, cast=int):
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    try:
        return cast(m.group(1))
    except ValueError:
        return None


def _parse_card(card) -> RawListing | None:
    cod_el = card.select_one(".cod-imovel strong")
    link = card.select_one("a.carousel-cell[href]")
    titulo = card.select_one(".card-titulo")
    valores = card.select_one(".card-valores")
    compl = card.select_one(".imo-dad-compl")
    local = card.select_one(".card-bairro-cidade-texto")
    if not cod_el and not link:
        return None

    # ".imo-dad-compl" -> "2 Dorm. 1 Banho 1 Garagem 48.96 m² A. Útil"
    compl_text = compl.get_text(" ", strip=True) if compl else ""
    bedrooms = _num(r"(\d+)\s*Dorm", compl_text)
    bathrooms = _num(r"(\d+)\s*Banho", compl_text)
    parking = _num(r"(\d+)\s*Garagem", compl_text)
    area = _num(r"([\d.]+)\s*m²", compl_text, float)  # decimal com ponto: 48.96

    url = link["href"] if link else None
    if url and not url.startswith("http"):
        url = f"{_BASE}/{url.lstrip('/')}"

    # Fotos do carrossel do card (lazy-load via data-flickity-lazyload-src).
    photos: list[str] = []
    for im in card.select("img"):
        src = (
            im.get("data-flickity-lazyload-src")
            or im.get("data-flickity-lazyload")
            or im.get("data-src")
            or im.get("src")
        )
        if src and src.startswith("http"):
            photos.append(src)
    photos = list(dict.fromkeys(photos))

    # ".card-bairro-cidade-texto" -> "Residencial Parati - São Carlos/SP"
    # (é o nome do condomínio + cidade; o bairro real não vem no card)
    address = None
    if local:
        address = local.get_text(strip=True).split(" - ")[0].strip() or None

    return RawListing(
        source="cardinali",
        source_id=cod_el.get_text(strip=True) if cod_el else None,
        url=url,
        title=titulo.get_text(" ", strip=True) if titulo else None,
        price=valores.get_text(" ", strip=True) if valores else None,  # "R$ 348.000,00 V"
        area_m2=area,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        parking_spots=parking,
        address=address,
        city="São Carlos",
        raw={"image": photos} if photos else None,
    )


class CardinaliCollector(Collector):
    name = "cardinali"

    def __init__(self, search_url: str | None = None) -> None:
        settings = get_settings()
        self.search_url = search_url or _SEARCH
        self.timeout = settings.request_timeout
        self.user_agent = settings.user_agent

    def _fetch_page(self, client: httpx.Client, page: int) -> list:
        params = {"pag": page} if page > 1 else None
        resp = client.get(self.search_url, params=params)
        resp.raise_for_status()
        html = resp.content.decode("iso-8859-1", errors="replace")
        return BeautifulSoup(html, "lxml").select(".muda_card1")

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        settings = get_settings()
        pages = max_pages or settings.collect_max_pages
        headers = {"User-Agent": self.user_agent, "Accept-Language": "pt-BR,pt;q=0.9"}
        seen: set[str] = set()
        out: list[RawListing] = []

        with httpx.Client(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            for page in range(1, pages + 1):
                try:
                    cards = self._fetch_page(client, page)
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.warning("Cardinali: falha na página %s: %s", page, exc)
                    break
                if not cards:
                    break
                new = 0
                for card in cards:
                    rl = _parse_card(card)
                    if rl is None:
                        continue
                    key = rl.source_id or rl.url or ""
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    out.append(rl)
                    new += 1
                logger.info("Cardinali: página %s -> %s anúncios", page, new)
                if new == 0:
                    break
        return out
