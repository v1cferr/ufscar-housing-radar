"""Coletor genérico da plataforma MSYS Imob (Next.js).

Várias imobiliárias de São Carlos rodam nesta plataforma white-label e
compartilham o MESMO frontend — um coletor cobre todas. Confirmadas:
Roca (roca.com.br), iPlano (iplano.com.br), Top Imóveis (topimoveissaocarlos.com.br).

Dois modos:
- **seed** (padrão): lê a listagem e pega os ~12 docs do SSR, em
  `props.initialProps.pageProps.template.data.initialPropertys.docs[]`. Rápido,
  1 requisição por imobiliária.
- **full** (`--full`): varre o sitemap `/sitemaps/propertys.xml`, filtra os
  apartamentos de São Carlos e abre cada página de detalhe (doc em
  `props.initialProps.pageProps.template.data.property`). Para ser educado:
  delay entre requisições, um `cap` por execução e pula ids já no banco
  (`skip_ids`) — então execuções repetidas cobrem o acervo progressivamente.

Todos os docs trazem preço, condomínio, área, quartos, banheiros, vagas, bairro
E latitude/longitude (dispensa geocoding).

robots.txt: listagem permitida (`Allow: /`), mas bloqueia o UA `Scrapy` —
por isso usamos um User-Agent de navegador.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata

import httpx
from bs4 import BeautifulSoup

from housing_radar.collectors.base import Collector
from housing_radar.config import get_settings
from housing_radar.models import RawListing

logger = logging.getLogger(__name__)

# Imobiliárias conhecidas nesta plataforma (domínio + nome curto p/ Listing.source).
# Todas compartilham o mesmo frontend MSYS (mesmo __NEXT_DATA__ e /sitemaps/propertys.xml).
MSYS_SITES: dict[str, str] = {
    "roca": "roca.com.br",
    "iplano": "iplano.com.br",
    "top": "topimoveissaocarlos.com.br",
    "e2": "imobiliariae2.com.br",
    "mariaaires": "mariaaires.com.br",
    "center": "centerimoveis.com",
}

_LISTING_PATH = ("props", "initialProps", "pageProps", "template", "data", "initialPropertys")
_PROPERTY_PATH = ("props", "initialProps", "pageProps", "template", "data", "property")
# URL de detalhe de apartamento À VENDA em São Carlos no sitemap.
# Inclui "venda" e "venda-e-locacao"; exclui "locacao" (aluguel puro).
_SC_APT_DETAIL = re.compile(
    r"/imovel/(?:venda-e-locacao|venda)/apartament[^/]*/sao-carlos/.+/(\d+)/?$"
)


def _slug(text: str | None) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "").lower()
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dig(node, path):
    for key in path:
        node = (node or {}).get(key, {})
    return node


class MSYSCollector(Collector):
    def __init__(
        self,
        domain: str,
        *,
        name: str,
        search_path: str = "/comprar/sao-carlos-sp/apartamentos",
        full: bool = False,
        cap: int = 300,
        delay: float = 0.4,
        skip_ids: set[str] | None = None,
    ) -> None:
        settings = get_settings()
        self.name = name
        self.domain = domain.rstrip("/")
        self.search_url = f"https://{self.domain}{search_path}"
        self.timeout = settings.request_timeout
        self.user_agent = settings.user_agent
        self.full = full
        self.cap = cap
        self.delay = delay
        self.skip_ids = skip_ids or set()

    @property
    def _headers(self) -> dict:
        return {"User-Agent": self.user_agent, "Accept-Language": "pt-BR,pt;q=0.9"}

    def _next_data(self, resp: httpx.Response) -> dict | None:
        tag = BeautifulSoup(resp.text, "lxml").find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            return None
        return json.loads(tag.string)

    def _parse(self, d: dict, url: str | None = None) -> RawListing:
        idt = d.get("idtProperty")
        if url is None:
            slug = _slug(f"{d.get('namDistrict') or ''} {d.get('namCondominium') or ''}")
            base = f"https://{self.domain}/imovel/venda/apartamentos/sao-carlos"
            url = (f"{base}/{slug}/{idt}" if slug else f"{base}/{idt}") if idt else None
        return RawListing(
            source=self.name,
            source_id=str(idt) if idt else None,
            url=url,
            title=d.get("desTitleSite"),
            price=d.get("valSales"),
            rent_price=d.get("valLocation") or None,  # aluguel, quando também loca
            condo_fee=d.get("valCondominium") or None,
            area_m2=_f(d.get("prop_char_2")),
            bedrooms=d.get("prop_char_5"),
            bathrooms=d.get("prop_char_176"),
            parking_spots=d.get("totalGarages"),
            neighborhood=d.get("namDistrict"),
            city=d.get("namCity") or "São Carlos",
            lat=_f(d.get("latitude")),
            lon=_f(d.get("longitude")),
            raw=d,
        )

    def _collect_seed(self) -> list[RawListing]:
        with httpx.Client(timeout=self.timeout, headers=self._headers, follow_redirects=True) as c:
            try:
                data = self._next_data(c.get(self.search_url))
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning("%s: falha na coleta: %s", self.name, exc)
                return []
        node = _dig(data, _LISTING_PATH) if data else {}
        docs = node.get("docs", []) or []
        out = [self._parse(d) for d in docs]
        logger.info(
            "%s: %s de %s anúncios (seed SSR; use --full p/ o acervo completo)",
            self.name,
            len(out),
            node.get("numFound", 0),
        )
        return out

    def _sc_apt_urls(self, client: httpx.Client) -> list[tuple[str, str]]:
        """(url, id) dos apartamentos de São Carlos no sitemap de imóveis."""
        resp = client.get(f"https://{self.domain}/sitemaps/propertys.xml")
        resp.raise_for_status()
        pairs = []
        for loc in re.findall(r"<loc>([^<]+)</loc>", resp.text):
            m = _SC_APT_DETAIL.search(loc)
            if m:
                pairs.append((loc, m.group(1)))
        return pairs

    def _collect_full(self) -> list[RawListing]:
        out: list[RawListing] = []
        with httpx.Client(timeout=self.timeout, headers=self._headers, follow_redirects=True) as c:
            try:
                pairs = self._sc_apt_urls(c)
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning("%s[full]: falha ao ler sitemap: %s", self.name, exc)
                return []
            todo = [(u, i) for (u, i) in pairs if i not in self.skip_ids][: self.cap]
            logger.info(
                "%s[full]: %s apt-SC no sitemap, %s já no banco -> buscando %s (cap %s, delay %ss)",
                self.name,
                len(pairs),
                len(pairs) - len([1 for _, i in pairs if i not in self.skip_ids]),
                len(todo),
                self.cap,
                self.delay,
            )
            for n, (url, _id) in enumerate(todo, 1):
                try:
                    data = self._next_data(c.get(url))
                    doc = _dig(data, _PROPERTY_PATH) if data else None
                    if doc and doc.get("idtProperty"):
                        out.append(self._parse(doc, url=url))
                except Exception as exc:  # noqa: BLE001 — best-effort, segue
                    logger.warning("%s[full]: falha em %s: %s", self.name, url, exc)
                if n % 50 == 0:
                    logger.info("%s[full]: %s/%s", self.name, n, len(todo))
                if self.delay:
                    time.sleep(self.delay)
        logger.info("%s[full]: %s anúncios coletados", self.name, len(out))
        return out

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        return self._collect_full() if self.full else self._collect_seed()


def make_msys(name: str) -> MSYSCollector:
    """Factory: cria o coletor MSYS (modo seed) de uma imobiliária conhecida."""
    return MSYSCollector(MSYS_SITES[name], name=name)
