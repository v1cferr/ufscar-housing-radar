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

# Imobiliárias do Grupo ZAP: domínio + caminho de busca (apartamentos em São Carlos),
# para venda e locação. O mesmo parser (JSON-LD Apartment) serve às duas transações.
GRUPOZAP_SITES: dict[str, dict[str, str]] = {
    "vivareal": {
        "domain": "www.vivareal.com.br",
        "search": "/venda/sp/sao-carlos/apartamento_residencial/",
        "search_aluguel": "/aluguel/sp/sao-carlos/apartamento_residencial/",
        # Kitnet/conjugado: a página usa JSON-LD @type "Product" (não "Apartment"),
        # mas com os mesmos campos (offers/floorSize/numberOfBedrooms/address).
        "search_kitnet": "/aluguel/sp/sao-carlos/kitnet_residencial/",
    },
    "zap": {
        "domain": "www.zapimoveis.com.br",
        "search": "/venda/apartamentos/sp+sao-carlos/",
        "search_aluguel": "/aluguel/apartamentos/sp+sao-carlos/",
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


def _listings(data, jsonld_type: str = "Apartment") -> list[dict]:
    """Coleta recursivamente os objetos JSON-LD do tipo dado. Apartamentos vêm como
    'Apartment'; kitnets/conjugados vêm como 'Product' (mesmos campos: offers,
    floorSize, numberOfBedrooms, address)."""
    out: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("@type") == jsonld_type:
                out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


class GrupoZapCollector(Collector):
    def __init__(
        self, name: str, transacao: str = "compra", tipo_imovel: str = "apartamento"
    ) -> None:
        if name not in GRUPOZAP_SITES:
            raise ValueError(f"Site Grupo ZAP desconhecido: {name}")
        site = GRUPOZAP_SITES[name]
        settings = get_settings()
        self.name = name
        self.transacao = transacao
        self.tipo_imovel = tipo_imovel
        self.domain = site["domain"]
        if tipo_imovel == "kitnet":
            path = site["search_kitnet"]
            self.jsonld_type = "Product"  # a página de kitnet usa @type Product
        else:
            path = site["search_aluguel"] if transacao == "aluguel" else site["search"]
            self.jsonld_type = "Apartment"
        self.search_url = f"https://{self.domain}{path}"
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
                items += _listings(json.loads(tag.string), self.jsonld_type)
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

        is_rent = self.transacao == "aluguel"
        price = offer.get("price")
        return RawListing(
            source=self.name,
            source_id=source_id,
            url=url,
            title=name or None,
            price=None if is_rent else price,
            rent_price=price if is_rent else None,
            transacao=self.transacao,
            tipo_imovel=self.tipo_imovel,
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


def make_grupozap(
    name: str, transacao: str = "compra", tipo_imovel: str = "apartamento"
) -> GrupoZapCollector:
    return GrupoZapCollector(name, transacao, tipo_imovel)
