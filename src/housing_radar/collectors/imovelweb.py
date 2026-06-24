"""Coletor Imovelweb (Navent) via Playwright — best-effort, OPCIONAL.

A Imovelweb fica atrás do Cloudflare e devolve 403 para httpx. Um browser real
(Playwright/Chromium) passa o desafio. Os dados vêm dos cards renderizados
(`data-qa="posting PROPERTY"`): id, link, preço, localização, features (área,
quartos, banheiros, vagas) e condomínio.

Este é o coletor "cirúrgico" de Playwright: o resto do projeto segue em httpx +
BeautifulSoup (mais leve/rápido para as fontes que entregam JSON embutido). O
Playwright só é usado aqui, onde realmente agrega (anti-bot + DOM renderizado).

OPCIONAL: requer o extra `browser` e o Chromium —
    uv sync --extra browser && uv run playwright install chromium
Sem isso o coletor apenas avisa e devolve lista vazia. NÃO entra na imagem Docker
que serve o site (rode no host). Por ser opcional/pesado, fica fora do `collect all`.

ATENÇÃO (ToS): scraping da Imovelweb é frágil e pode violar os termos. Use em
baixo volume, para triagem pessoal (V1C-68). É Navent (mesma base de VivaReal/ZAP),
então há bastante sobreposição — vale mais como conferência cruzada de preços.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from housing_radar.collectors.base import Collector
from housing_radar.config import get_settings
from housing_radar.models import RawListing

logger = logging.getLogger(__name__)

_BASE = "https://www.imovelweb.com.br"
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_ID_RE = re.compile(r"(\d+)\.html")
_AREA_RE = re.compile(r"([\d.,]+)\s*m²")
_BED_RE = re.compile(r"(\d+)\s*quart", re.I)
_BATH_RE = re.compile(r"(\d+)\s*ban", re.I)
_PARK_RE = re.compile(r"(\d+)\s*vaga", re.I)


def _norm_city(value: str | None) -> str:
    nfkd = unicodedata.normalize("NFKD", (value or "").lower())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


class ImovelWebCollector(Collector):
    name = "imovelweb"

    def __init__(self, *, headless: bool = True) -> None:
        settings = get_settings()
        self.headless = headless
        self.page_wait_ms = 5000
        self.target_city = "sao carlos"
        _ = settings  # mantém simetria com os outros coletores

    def _search_url(self, page: int) -> str:
        suffix = "" if page <= 1 else f"-pagina-{page}"
        return f"{_BASE}/apartamentos-venda-sao-carlos-sp{suffix}.html"

    @staticmethod
    def _qa(card, name: str) -> str | None:
        loc = card.locator(f'[data-qa="{name}"]')
        return loc.first.inner_text().strip() if loc.count() else None

    def _parse_card(self, card) -> RawListing | None:
        data_id = card.get_attribute("data-id")
        href = None
        links = card.locator("a")
        if links.count():
            href = links.first.get_attribute("href")
        url = f"{_BASE}{href.split('?')[0]}" if href else None
        source_id = data_id
        if not source_id and url:
            m = _ID_RE.search(url)
            source_id = m.group(1) if m else None

        loc_txt = self._qa(card, "POSTING_CARD_LOCATION") or ""
        if "," in loc_txt:
            neighborhood, city = (x.strip() for x in loc_txt.rsplit(",", 1))
        else:
            neighborhood, city = (loc_txt.strip() or None), "São Carlos"
        if _norm_city(city) != self.target_city:
            return None  # ignora cidade vizinha eventualmente injetada

        feats = self._qa(card, "POSTING_CARD_FEATURES") or ""

        def grab(rx: re.Pattern) -> str | None:
            m = rx.search(feats)
            return m.group(1) if m else None

        # Fotos do card (URLs no HTML; ignora data: placeholders).
        photos: list[str] = []
        try:
            photos = list(dict.fromkeys(
                re.findall(r"https?://[^\"'\s)]+?\.(?:jpg|jpeg|png|webp)", card.inner_html(), re.I)
            ))
        except Exception:  # noqa: BLE001 — best-effort
            photos = []

        raw = {"location": loc_txt, "features": feats}
        if photos:
            raw["image"] = photos
        return RawListing(
            source="imovelweb",
            source_id=source_id,
            url=url,
            title=self._qa(card, "POSTING_CARD_DESCRIPTION"),
            # parse_money/parse_float (normalize.py) limpam "R$ 795.000", "109 m²", etc.
            price=self._qa(card, "POSTING_CARD_PRICE"),
            condo_fee=self._qa(card, "expensas"),
            area_m2=grab(_AREA_RE),
            bedrooms=grab(_BED_RE),
            bathrooms=grab(_BATH_RE),
            parking_spots=grab(_PARK_RE),
            neighborhood=neighborhood,
            city=city,
            raw=raw,
        )

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        try:
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError:
            logger.warning(
                "imovelweb: Playwright ausente. Rode: "
                "uv sync --extra browser && uv run playwright install chromium"
            )
            return []

        pages = max_pages or get_settings().collect_max_pages
        seen: set[str] = set()
        out: list[RawListing] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            ctx = browser.new_context(
                user_agent=_UA, locale="pt-BR", viewport={"width": 1366, "height": 900}
            )
            page = ctx.new_page()
            try:
                for n in range(1, pages + 1):
                    # O Cloudflare às vezes desafia páginas seguintes — tenta 2x antes
                    # de desistir (best-effort: a página 1 quase sempre passa).
                    loaded = False
                    for attempt in range(2):
                        try:
                            page.goto(self._search_url(n), wait_until="domcontentloaded", timeout=45000)
                            page.wait_for_selector('[data-qa="posting PROPERTY"]', timeout=20000)
                            page.wait_for_timeout(self.page_wait_ms)
                            loaded = True
                            break
                        except Exception as exc:  # noqa: BLE001 — best-effort
                            logger.warning(
                                "imovelweb: sem cards na página %s (tentativa %s): %s", n, attempt + 1, exc
                            )
                            page.wait_for_timeout(3000)
                    if not loaded:
                        break
                    cards = page.locator('[data-qa="posting PROPERTY"]')
                    count = cards.count()
                    new = 0
                    for i in range(count):
                        try:
                            raw = self._parse_card(cards.nth(i))
                        except Exception:  # noqa: BLE001 — card isolado, segue
                            continue
                        if raw is None:
                            continue
                        key = raw.source_id or raw.url or ""
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        out.append(raw)
                        new += 1
                    logger.info("imovelweb: página %s -> %s anúncios em São Carlos", n, new)
                    if new == 0 and n > 1:
                        break
            finally:
                browser.close()

        logger.info("imovelweb: %s anúncios coletados", len(out))
        return out
