"""Custo total mensal estimado por imóvel (V1C-68).

O eixo que permite comparar opções de forma justa dentro de cada transação:

- **aluguel**: aluguel + condomínio + IPTU + contas (água/luz/internet).
- **compra**: parcela (Tabela Price) + condomínio + IPTU + contas.

O que é informado pelo anúncio (aluguel, condomínio, preço) entra direto; o que
não vem dos portais (IPTU, contas) é **estimativa configurável** em `CostConfig`,
claramente sinalizada no breakdown. A parcela usa os mesmos params do simulador
do dashboard (entrada/juros/prazo) — a calibração real pende do CLT do usuário.

Função pura, sem banco: quem chama injeta a `CostConfig` (ex.: o export monta a
partir das settings salvas).
"""

from __future__ import annotations

from dataclasses import dataclass

from housing_radar.models import Listing


@dataclass
class CostConfig:
    # Financiamento (espelha os defaults de _DEFAULT_SETTINGS no app).
    entrada_pct: float = 20.0
    juros_aa: float = 11.0
    prazo_meses: int = 360
    # Estimativas sem dado por imóvel (ajustar conforme a realidade de São Carlos).
    contas_mensal: float = 250.0  # água + luz + internet, morando solo
    iptu_aa_pct: float = 0.8  # IPTU anual como % do valor (compra)
    iptu_aluguel_mensal: float = 0.0  # IPTU no aluguel (muitas vezes incluso/variável)


def parcela_price(
    price: float | None, entrada_pct: float, juros_aa: float, prazo_meses: int
) -> float | None:
    """Parcela mensal por Tabela Price. Espelha o cálculo do dashboard (index.html).

    Juros a.a. convertido para mensal equivalente: (1+jaa)^(1/12) - 1.
    """
    if not price or price <= 0 or prazo_meses <= 0:
        return None
    financiado = price * (1 - entrada_pct / 100)
    if financiado <= 0:
        return 0.0
    i = (1 + juros_aa / 100) ** (1 / 12) - 1
    if i <= 0:
        return financiado / prazo_meses
    return financiado * i / (1 - (1 + i) ** (-prazo_meses))


def monthly_cost(listing: Listing, config: CostConfig | None = None) -> tuple[float | None, dict]:
    """Custo total mensal estimado + breakdown transparente por componente.

    Retorna (None, ...) quando não há base (aluguel ou parcela) para estimar.
    """
    cfg = config or CostConfig()
    parts: dict[str, float] = {}

    if listing.transacao == "aluguel":
        if listing.rent_price:
            parts["aluguel"] = round(listing.rent_price, 2)
        if cfg.iptu_aluguel_mensal:
            parts["iptu"] = round(cfg.iptu_aluguel_mensal, 2)
    else:  # compra (default)
        parcela = parcela_price(listing.price, cfg.entrada_pct, cfg.juros_aa, cfg.prazo_meses)
        if parcela is not None:
            parts["parcela"] = round(parcela, 2)
        if listing.price:
            parts["iptu"] = round(listing.price * cfg.iptu_aa_pct / 100 / 12, 2)

    if listing.condo_fee:
        parts["condominio"] = round(listing.condo_fee, 2)
    # Repúblicas: o aluguel já é all-in (água/luz/internet, às vezes refeição) —
    # somar contas estimadas superestimaria. Apto/kitnet pagam contas à parte.
    if cfg.contas_mensal and listing.tipo_imovel != "quarto_republica":
        parts["contas"] = round(cfg.contas_mensal, 2)

    # Sem aluguel nem parcela não há custo mensal significativo a estimar.
    if not parts.get("aluguel") and not parts.get("parcela"):
        return None, {"parts": parts, "note": "sem aluguel/parcela p/ estimar custo mensal"}

    total = round(sum(parts.values()), 2)
    return total, {"parts": parts, "total": total}


def cost_config_from_settings(settings: dict | None) -> CostConfig:
    """Monta a CostConfig a partir das settings salvas (financiamento); o resto fica default."""
    cfg = CostConfig()
    if not settings:
        return cfg
    for key, attr, cast in (
        ("entrada_pct", "entrada_pct", float),
        ("juros_aa", "juros_aa", float),
        ("prazo_meses", "prazo_meses", int),
        ("iptu_aa", "iptu_aa_pct", float),
        ("contas", "contas_mensal", float),
    ):
        raw = settings.get(key)
        if raw not in (None, ""):
            try:
                setattr(cfg, attr, cast(raw))
            except (TypeError, ValueError):
                pass
    return cfg
