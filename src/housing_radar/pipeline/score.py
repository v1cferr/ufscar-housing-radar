"""Score 0–100 transparente e configurável, por transação (compra | aluguel).

Cada critério vira um sub-score 0–1 por interpolação linear entre âncoras
(melhor/pior). O score final é a média ponderada dos sub-scores *disponíveis*
(critérios sem dado são ignorados e os pesos restantes são renormalizados),
para que um anúncio sem condomínio informado não seja punido injustamente.

O caminho é escolhido por `listing.transacao` (V1C-68):

- **compra**: critério de preço usa o valor de venda (âncoras 250k–600k) e há
  o bônus de custo-benefício preço/aluguel. Comportamento histórico preservado.
- **aluguel**: critério de "rent" usa o aluguel mensal (âncoras próprias), sem
  o piso de venda e sem o bônus preço/aluguel — senão um aluguel cairia no
  `price_floor` de venda e viraria score-lixo.

Scores só comparam dentro da mesma transação. A comparação ENTRE categorias é
papel da classificação estratégica (`estrategia`), não do score.

Os pesos e âncoras são valores de partida pensados para São Carlos/UFSCar —
a ideia é calibrar conforme você e sua mãe avaliam os imóveis reais.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from housing_radar.models import Listing


def _lerp(value: float, good: float, bad: float) -> float:
    """Interpola para 0–1. `good` -> 1.0, `bad` -> 0.0 (good pode ser < ou > bad)."""
    if good == bad:
        return 0.5
    score = (value - bad) / (good - bad)
    return max(0.0, min(1.0, score))


@dataclass
class ScoreConfig:
    # Pesos relativos por transação (não precisam somar 1 — são renormalizados
    # sobre os critérios com dado disponível).
    weights_compra: dict[str, float] = field(
        default_factory=lambda: {
            "proximity": 0.30,
            "price": 0.25,
            "area": 0.15,
            "condo_fee": 0.10,
            "bedrooms": 0.10,
            "parking": 0.10,
            # Bônus LEVE de custo-benefício (relação preço/aluguel). Só conta quando
            # o anúncio também informa aluguel; senão o peso é renormalizado fora.
            "value": 0.08,
        }
    )
    # Aluguel: morar solo perto da UFSCar -> proximidade e custo mandam; área e
    # quartos pesam menos (sem o bônus value, que é de compra).
    weights_aluguel: dict[str, float] = field(
        default_factory=lambda: {
            "proximity": 0.35,
            "rent": 0.30,
            "condo_fee": 0.12,
            "area": 0.10,
            "bedrooms": 0.05,
            "parking": 0.08,
        }
    )
    # Preço de venda abaixo disto é implausível p/ apartamento em São Carlos —
    # quase sempre é aluguel mal rotulado ou erro de dado. Subscore de preço = 0.
    price_floor: float = 50_000
    # Aluguel mensal abaixo disto é dado implausível (erro/ruído).
    rent_floor: float = 200
    # Âncoras (melhor, pior).
    dist_km_good_bad: tuple[float, float] = (1.5, 8.0)
    price_good_bad: tuple[float, float] = (250_000, 600_000)
    # Aluguel mensal: ~R$900 ótimo p/ morar solo perto do campus; >= R$2500 ruim.
    rent_good_bad: tuple[float, float] = (900.0, 2500.0)
    area_good_bad: tuple[float, float] = (80.0, 40.0)
    condo_good_bad: tuple[float, float] = (300.0, 900.0)
    # Relação preço/aluguel em ANOS (preço ÷ aluguel anual). ~12 anos = compra
    # ótima frente ao aluguel; >= 25 anos = alugar compensa bem mais.
    price_to_rent_good_bad: tuple[float, float] = (12.0, 25.0)
    # Proximidade é o critério central. Sem geocoding não dá pra confiar que é
    # perto da UFSCar — então atribui um subscore baixo (penaliza) em vez de
    # ignorar o critério, senão um anúncio sem localização sobe ao topo de graça.
    proximity_unknown: float = 0.25

    def bedroom_score(self, n: int) -> float:
        return {1: 0.40, 2: 1.0, 3: 0.90}.get(n, 0.70 if n >= 4 else 0.30)

    def parking_score(self, n: int) -> float:
        return 0.30 if n <= 0 else 1.0


def _common_subscores(listing: Listing, cfg: ScoreConfig) -> dict[str, float]:
    """Sub-scores compartilhados entre compra e aluguel (sem preço/aluguel)."""
    subs: dict[str, float] = {}
    if listing.dist_ufscar_km is not None:
        subs["proximity"] = _lerp(listing.dist_ufscar_km, *cfg.dist_km_good_bad)
    else:
        # Sem localização: penaliza (não pode disputar o topo com quem é perto).
        subs["proximity"] = cfg.proximity_unknown
    if listing.area_m2 is not None:
        subs["area"] = _lerp(listing.area_m2, *cfg.area_good_bad)
    if listing.condo_fee is not None:
        subs["condo_fee"] = _lerp(listing.condo_fee, *cfg.condo_good_bad)
    if listing.bedrooms is not None:
        subs["bedrooms"] = cfg.bedroom_score(listing.bedrooms)
    if listing.parking_spots is not None:
        subs["parking"] = cfg.parking_score(listing.parking_spots)
    return subs


def _finalize(subs: dict[str, float], weights: dict[str, float]) -> tuple[float | None, dict]:
    if not subs:
        return None, {"subscores": {}, "note": "sem dados suficientes para score"}
    total_weight = sum(weights[k] for k in subs)
    final = sum(subs[k] * weights[k] for k in subs) / total_weight * 100
    breakdown = {
        "subscores": {k: round(v, 3) for k, v in subs.items()},
        "weights": {k: weights[k] for k in subs},
        "missing": [k for k in weights if k not in subs],
        "final": round(final, 1),
    }
    return round(final, 1), breakdown


def _score_aluguel(listing: Listing, cfg: ScoreConfig) -> tuple[float | None, dict]:
    subs = _common_subscores(listing, cfg)
    if listing.rent_price is not None:
        if listing.rent_price < cfg.rent_floor:
            subs["rent"] = 0.0  # implausível p/ aluguel (erro/ruído)
        else:
            subs["rent"] = _lerp(listing.rent_price, *cfg.rent_good_bad)
    return _finalize(subs, cfg.weights_aluguel)


def _score_compra(listing: Listing, cfg: ScoreConfig) -> tuple[float | None, dict]:
    subs = _common_subscores(listing, cfg)
    if listing.price is not None:
        if listing.price < cfg.price_floor:
            subs["price"] = 0.0  # implausível p/ venda (provável aluguel/erro)
        else:
            subs["price"] = _lerp(listing.price, *cfg.price_good_bad)
    # Custo-benefício: só quando há venda plausível E aluguel informado.
    if (
        listing.price is not None
        and listing.price >= cfg.price_floor
        and listing.rent_price
        and listing.rent_price > 0
    ):
        years = listing.price / (listing.rent_price * 12)
        subs["value"] = _lerp(years, *cfg.price_to_rent_good_bad)
    return _finalize(subs, cfg.weights_compra)


def score_listing(listing: Listing, config: ScoreConfig | None = None) -> tuple[float | None, dict]:
    """Calcula o score 0–100 escolhendo o perfil pela transação do anúncio."""
    cfg = config or ScoreConfig()
    if listing.transacao == "aluguel":
        return _score_aluguel(listing, cfg)
    return _score_compra(listing, cfg)  # compra (default) = comportamento histórico
