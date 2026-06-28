"""Modelos de dados: anúncio bruto (coletor) e o registro persistido (Listing)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


# Categorização do funil de decisão habitacional (V1C-68). Dois eixos ortogonais
# (transação x tipo de imóvel) + a classificação estratégica manual. As "abas" da
# UI são filtros sobre esses campos, não categorias soltas.
TRANSACOES = ("compra", "aluguel")
TIPOS_IMOVEL = ("apartamento", "kitnet", "quarto_republica", "casa")
ESTRATEGIAS = ("base_imediata", "ponte", "patrimonial", "descartar")


class RawListing(BaseModel):
    """Saída de um coletor, antes da normalização do pipeline.

    Campos propositalmente frouxos: cada portal entrega o que consegue.
    """

    source: str
    source_id: str | None = None
    url: str | None = None
    title: str | None = None

    # Valores podem chegar como string ("R$ 350.000") — normalize.py limpa.
    price: float | str | None = None
    rent_price: float | str | None = None  # aluguel mensal (quando também loca)
    condo_fee: float | str | None = None
    area_m2: float | str | None = None
    bedrooms: int | str | None = None
    bathrooms: int | str | None = None
    parking_spots: int | str | None = None

    address: str | None = None
    neighborhood: str | None = None
    city: str | None = "São Carlos"
    lat: float | None = None
    lon: float | None = None

    # Categorização (quando o coletor souber; senão o normalize assume o default).
    transacao: str | None = None  # compra | aluguel
    tipo_imovel: str | None = None  # apartamento | kitnet | quarto_republica | casa

    description: str | None = None
    raw: dict | None = None


class Listing(SQLModel, table=True):
    """Anúncio normalizado, geolocalizado, com tempo até a UFSCar e score."""

    id: int | None = Field(default=None, primary_key=True)

    # Identidade / proveniência
    source: str = Field(index=True)
    source_id: str | None = Field(default=None, index=True)
    url: str | None = None
    dedupe_key: str = Field(index=True)

    # Categoria / estratégia (funil de decisão — V1C-68). Valores válidos em
    # TRANSACOES / TIPOS_IMOVEL / ESTRATEGIAS. Default compra/apartamento mantém
    # o acervo atual (só venda de apto) coerente; as abas da UI filtram por aqui.
    transacao: str = Field(default="compra", index=True)
    tipo_imovel: str = Field(default="apartamento", index=True)
    estrategia: str | None = Field(default=None, index=True)

    # Atributos do imóvel
    title: str | None = None
    price: float | None = Field(default=None, index=True)
    rent_price: float | None = None  # aluguel mensal (anúncios de venda-e-locação)
    condo_fee: float | None = None
    area_m2: float | None = None
    bedrooms: int | None = Field(default=None, index=True)
    bathrooms: int | None = None
    parking_spots: int | None = None

    # Localização
    address: str | None = None
    neighborhood: str | None = Field(default=None, index=True)
    city: str | None = "São Carlos"
    lat: float | None = None
    lon: float | None = None
    geocoded: bool = False

    # Deslocamento até a UFSCar
    dist_ufscar_km: float | None = None
    time_walk_min: float | None = None
    time_bike_min: float | None = None
    time_car_min: float | None = None
    travel_provider: str | None = None  # "ors" | "estimate"

    # Score
    score: float | None = Field(default=None, index=True)
    score_breakdown: dict | None = Field(default=None, sa_column=Column(JSON))

    description: str | None = None
    raw: dict | None = Field(default=None, sa_column=Column(JSON))

    status: str = Field(default="active", index=True)  # active | archived
    favorite: bool = Field(default=False, index=True)  # marcado por mim/minha mãe
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class Setting(SQLModel, table=True):
    """Config compartilhada (key/value) — ex.: parcela máxima, origem da mudança.

    Sem login (uso pessoal eu + mãe); o estado persiste entre sessões/dispositivos.
    """

    key: str = Field(primary_key=True)
    value: str = ""
