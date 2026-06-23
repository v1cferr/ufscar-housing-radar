"""Score 0–100 transparente e configurável.

Cada critério vira um sub-score 0–1 por interpolação linear entre âncoras
(melhor/pior). O score final é a média ponderada dos sub-scores *disponíveis*
(critérios sem dado são ignorados e os pesos restantes são renormalizados),
para que um anúncio sem condomínio informado não seja punido injustamente.

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
    # Pesos relativos (não precisam somar 1 — são renormalizados).
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "proximity": 0.30,
            "price": 0.25,
            "area": 0.15,
            "condo_fee": 0.10,
            "bedrooms": 0.10,
            "parking": 0.10,
        }
    )
    # Preço de venda abaixo disto é implausível p/ apartamento em São Carlos —
    # quase sempre é aluguel mal rotulado ou erro de dado. Subscore de preço = 0.
    price_floor: float = 50_000
    # Âncoras (melhor, pior).
    dist_km_good_bad: tuple[float, float] = (1.5, 8.0)
    price_good_bad: tuple[float, float] = (250_000, 600_000)
    area_good_bad: tuple[float, float] = (80.0, 40.0)
    condo_good_bad: tuple[float, float] = (300.0, 900.0)

    def bedroom_score(self, n: int) -> float:
        return {1: 0.40, 2: 1.0, 3: 0.90}.get(n, 0.70 if n >= 4 else 0.30)

    def parking_score(self, n: int) -> float:
        return 0.30 if n <= 0 else 1.0


def score_listing(listing: Listing, config: ScoreConfig | None = None) -> tuple[float | None, dict]:
    cfg = config or ScoreConfig()
    subs: dict[str, float] = {}

    if listing.dist_ufscar_km is not None:
        subs["proximity"] = _lerp(listing.dist_ufscar_km, *cfg.dist_km_good_bad)
    if listing.price is not None:
        if listing.price < cfg.price_floor:
            subs["price"] = 0.0  # implausível p/ venda (provável aluguel/erro)
        else:
            subs["price"] = _lerp(listing.price, *cfg.price_good_bad)
    if listing.area_m2 is not None:
        subs["area"] = _lerp(listing.area_m2, *cfg.area_good_bad)
    if listing.condo_fee is not None:
        subs["condo_fee"] = _lerp(listing.condo_fee, *cfg.condo_good_bad)
    if listing.bedrooms is not None:
        subs["bedrooms"] = cfg.bedroom_score(listing.bedrooms)
    if listing.parking_spots is not None:
        subs["parking"] = cfg.parking_score(listing.parking_spots)

    if not subs:
        return None, {"subscores": {}, "note": "sem dados suficientes para score"}

    total_weight = sum(cfg.weights[k] for k in subs)
    final = sum(subs[k] * cfg.weights[k] for k in subs) / total_weight * 100

    breakdown = {
        "subscores": {k: round(v, 3) for k, v in subs.items()},
        "weights": {k: cfg.weights[k] for k in subs},
        "missing": [k for k in cfg.weights if k not in subs],
        "final": round(final, 1),
    }
    return round(final, 1), breakdown
