"""Coletor genérico do Grupo ZAP: VivaReal e ZAP Imóveis (best-effort).

As duas plataformas são da mesma empresa e compartilham o frontend — e, melhor:
a **página de busca já embute os ~30 anúncios em `<script type="application/ld+json">`**
como objetos `Apartment` (com preço em `offers.price`, área em `floorSize`, quartos,
banheiros e endereço). Então não dependemos de API interna nem de furar anti-bot:
lê-se o dado estruturado público da própria página (mesma filosofia do `__NEXT_DATA__`
do MSYS). Paginação via `?pagina=N`.

O JSON-LD não traz lat/lon nem condomínio — o pipeline geocoda pelo endereço.

ATENÇÃO (ToS): scraping destas plataformas é frágil e pode violar os termos de uso.
Uso em BAIXO volume e para triagem pessoal (card V1C-68). Se o layout/anti-bot mudar,
este coletor degrada com elegância e o pipeline segue pelas outras fontes.
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

# Imobiliárias do Grupo ZAP: domínio + caminho de busca (apartamentos à venda, São Carlos).
GRUPOZAP_SITES: dict[str, dict[str, str]] = {
    "vivareal": {
        "domain": "www.vivareal.com.br",
        "search": "/venda/sp/sao-carlos/apartamento_residencial/",
    },
    "zap": {
        "domain": "www.zapimoveis.com.br",
        "search": "/venda/apartamentos/sp+sao-carlos/",
    },
}

_ID_RE = re.compile(r"id-(\d+)")
# Bairro vem no fim do "name": "... em <BAIRRO>, São Carlos".
_HOOD_RE = re.compile(r"\bem\s+(.+?),\s*S[ãa]o Carlos", re.I)
_PARK_RE = re.compile(r"(\d+)\s*vaga", re.I)
# UA de navegador (as plataformas bloqueiam UAs de bot/Scrapy).
_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def _norm_city(value: str | None) -> str:
    nfkd = unicodedata.normalize("NFKD", (value or "").lower())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def _apartments(data) -> list[dict]:
    """Coleta recursivamente todo objeto JSON-LD com @type 'Apartment'."""
    out: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("@type") == "Apartment":
                out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


class GrupoZapCollector(Collector):
    def __init__(self, name: str) -> None:
        if name not in GRUPOZAP_SITES:
            raise ValueError(f"Site Grupo ZAP desconhecido: {name}")
        site = GRUPOZAP_SITES[name]
        settings = get_settings()
        self.name = name
        self.domain = site["domain"]
        self.search_url = f"https://{self.domain}{site['search']}"
        self.timeout = settings.request_timeout
        self.target_city = "sao carlos"

    @property
    def _headers(self) -> dict:
        return {"User-Agent": _UA, "Accept-Language": "pt-BR,pt;q=0.9"}

    def _page_apartments(self, client: httpx.Client, page: int) -> list[dict]:
        params = {"pagina": page} if page > 1 else None
        resp = client.get(self.search_url, params=params)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        items: list[dict] = []
        for tag in soup.find_all("script", type="application/ld+json"):
            if not tag.string:
                continue
            try:
                items += _apartments(json.loads(tag.string))
            except (ValueError, TypeError):
                continue
        return items

    def _parse(self, a: dict) -> RawListing | None:
        offer = a.get("offers") or {}
        url = offer.get("url") or a.get("url")
        m = _ID_RE.search(url or "")
        source_id = m.group(1) if m else None

        addr = a.get("address") or {}
        if _norm_city(addr.get("addressLocality")) != self.target_city:
            return None  # ignora anúncio patrocinado de outra cidade

        name = a.get("name") or ""
        hood = _HOOD_RE.search(name)
        floor = a.get("floorSize") or {}
        park = _PARK_RE.search(name)

        return RawListing(
            source=self.name,
            source_id=source_id,
            url=url,
            title=name or None,
            price=offer.get("price"),
            area_m2=floor.get("value"),
            bedrooms=a.get("numberOfBedrooms") or a.get("numberOfRooms"),
            bathrooms=a.get("numberOfBathroomsTotal"),
            parking_spots=int(park.group(1)) if park else None,
            address=addr.get("streetAddress"),
            neighborhood=hood.group(1).strip() if hood else None,
            city=addr.get("addressLocality") or "São Carlos",
            raw=a,
        )

    def collect(self, *, max_pages: int | None = None) -> list[RawListing]:
        pages = max_pages or get_settings().collect_max_pages
        seen: set[str] = set()
        out: list[RawListing] = []
        with httpx.Client(
            timeout=self.timeout, headers=self._headers, follow_redirects=True
        ) as client:
            for page in range(1, pages + 1):
                try:
                    apts = self._page_apartments(client, page)
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.warning("%s: falha na página %s: %s", self.name, page, exc)
                    break
                new = 0
                for a in apts:
                    listing = self._parse(a)
                    if listing is None:
                        continue
                    key = listing.source_id or listing.url or ""
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    out.append(listing)
                    new += 1
                logger.info("%s: página %s -> %s anúncios em São Carlos", self.name, page, new)
                if new == 0 and page > 1:
                    break
        logger.info("%s: %s anúncios coletados", self.name, len(out))
        return out


def make_grupozap(name: str) -> GrupoZapCollector:
    return GrupoZapCollector(name)
