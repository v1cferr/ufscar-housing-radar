"""Coletor genérico da plataforma MSYS Imob (Next.js).

Várias imobiliárias de São Carlos rodam nesta plataforma white-label e
compartilham o MESMO frontend — um coletor cobre todas. Confirmadas:
Roca (roca.com.br), iPlano (iplano.com.br), Top Imóveis (topimoveissaocarlos.com.br).

Os imóveis vêm embutidos no `<script id="__NEXT_DATA__">`, em:
    props.initialProps.pageProps.template.data.initialPropertys.docs[]
Cada doc traz preço, condomínio, área, quartos, banheiros, vagas, bairro
E **latitude/longitude** (dispensa geocoding).

⚠️ Limitação: a paginação por URL é ignorada no SSR (vêm só os ~12 docs
iniciais). O acervo completo exige varrer o sitemap (`/sitemaps/propertys.xml`)
e abrir cada detalhe — fica como modo futuro. Por ora coletamos o seed.

robots.txt: listagem permitida (`Allow: /`), mas bloqueia o UA `Scrapy` —
por isso usamos um User-Agent de navegador.
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

# Imobiliárias conhecidas nesta plataforma (domínio + nome curto p/ Listing.source).
MSYS_SITES: dict[str, str] = {
    "roca": "roca.com.br",
    "iplano": "iplano.com.br",
    "top": "topimoveissaocarlos.com.br",
}


def _slug(text: str | None) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "").lower()
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class MSYSCollector(Collector):
    _JSON_PATH = ("props", "initialProps", "pageProps", "template", "data", "initialPropertys")

    def __init__(
        self,
        domain: str,
        *,
        name: str,
        search_path: str = "/comprar/sao-carlos-sp/apartamentos",
    ) -> None:
        settings = get_settings()
        self.name = name
        self.domain = domain.rstrip("/")
        self.search_url = f"https://{self.domain}{search_path}"
        self.timeout = settings.request_timeout
        self.user_agent = settings.user_agent

    def _fetch_docs(self, client: httpx.Client) -> tuple[list[dict], int]:
        resp = client.get(self.search_url)
        resp.raise_for_status()
        tag = BeautifulSoup(resp.text, "lxml").find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            logger.warning("%s: __NEXT_DATA__ ausente (layout mudou?)", self.name)
            return [], 0
        node = json.loads(tag.string)
        for key in self._JSON_PATH:
            node = (node or {}).get(key, {})
        return node.get("docs", []) or [], node.get("numFound", 0)

    def _parse(self, d: dict) -> RawListing:
        idt = d.get("idtProperty")
        slug = _slug(f"{d.get('namDistrict') or ''} {d.get('namCondominium') or ''}")
        url = None
        if idt:
            base = f"https://{self.domain}/imovel/venda/apartamentos/sao-carlos"
            url = f"{base}/{slug}/{idt}" if slug else f"{base}/{idt}"
        return RawListing(
            source=self.name,
            source_id=str(idt) if idt else None,
            url=url,
            title=d.get("desTitleSite"),
            price=d.get("valSales"),
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

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        headers = {"User-Agent": self.user_agent, "Accept-Language": "pt-BR,pt;q=0.9"}
        with httpx.Client(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            try:
                docs, total = self._fetch_docs(client)
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning("%s: falha na coleta: %s", self.name, exc)
                return []
        out = [self._parse(d) for d in docs]
        logger.info(
            "%s: %s de %s anúncios (seed SSR; acervo completo via sitemap depois)",
            self.name,
            len(out),
            total,
        )
        return out


def make_msys(name: str) -> MSYSCollector:
    """Factory: cria o coletor MSYS de uma imobiliária conhecida pelo nome curto."""
    return MSYSCollector(MSYS_SITES[name], name=name)
