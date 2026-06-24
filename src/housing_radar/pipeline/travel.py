"""Cálculo de deslocamento até a UFSCar.

Dois provedores:
- "ors": OpenRouteService (real, por modal) — se HR_ORS_API_KEY estiver setada
  e o pacote opcional `openrouteservice` instalado (`uv sync --extra ors`).
- "estimate": fallback sem dependências externas — distância geodésica corrigida
  por um fator de desvio viário + velocidade média por modal.

Ônibus (transporte público) NÃO é coberto aqui: rotas de transit exigem GTFS
de São Carlos ou a API do Google Distance Matrix. Fica como próximo passo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from geopy.distance import geodesic

from housing_radar.config import get_settings

logger = logging.getLogger(__name__)

# Fator de desvio: ruas reais são mais longas que a linha reta.
_DETOUR_FACTOR = 1.35
# Velocidades médias urbanas (km/h) para a estimativa.
_SPEED_KMH = {"walk": 4.8, "bike": 14.0, "car": 28.0}
# Perfis do OpenRouteService por modal.
_ORS_PROFILE = {"walk": "foot-walking", "bike": "cycling-regular", "car": "driving-car"}


@dataclass
class TravelResult:
    dist_km: float | None
    time_walk_min: float | None
    time_bike_min: float | None
    time_car_min: float | None
    provider: str


def _estimate(origin: tuple[float, float], dest: tuple[float, float]) -> TravelResult:
    straight_km = geodesic(origin, dest).km
    road_km = straight_km * _DETOUR_FACTOR
    return TravelResult(
        dist_km=round(road_km, 2),
        time_walk_min=round(road_km / _SPEED_KMH["walk"] * 60, 1),
        time_bike_min=round(road_km / _SPEED_KMH["bike"] * 60, 1),
        time_car_min=round(road_km / _SPEED_KMH["car"] * 60, 1),
        provider="estimate",
    )


class TravelCalculator:
    def __init__(self, use_ors: bool = False) -> None:
        # ORS é opt-in (use_ors=True). A cota grátis da HeiGIT é pequena
        # (~2000 req/dia, ~40/min) e cada imóvel custa 3 chamadas — então o
        # ORS só deve refinar um subconjunto pequeno, nunca o acervo inteiro.
        settings = get_settings()
        self.dest = (settings.ufscar_lat, settings.ufscar_lon)
        self._ors = None
        if use_ors and settings.ors_api_key:
            try:
                import openrouteservice  # type: ignore

                self._ors = openrouteservice.Client(
                    key=settings.ors_api_key, base_url=settings.ors_base_url
                )
            except ImportError:
                logger.warning(
                    "HR_ORS_API_KEY setada mas pacote 'openrouteservice' ausente. "
                    "Rode `uv sync --extra ors`. Usando estimativa."
                )

    @property
    def ors_enabled(self) -> bool:
        return self._ors is not None

    def _via_ors(self, origin: tuple[float, float]) -> TravelResult | None:
        try:
            # ORS usa (lon, lat).
            coords = [(origin[1], origin[0]), (self.dest[1], self.dest[0])]
            times: dict[str, float] = {}
            dist_km: float | None = None
            for mode, profile in _ORS_PROFILE.items():
                route = self._ors.directions(coords, profile=profile)
                summary = route["routes"][0]["summary"]
                times[mode] = round(summary["duration"] / 60, 1)
                if mode == "car":
                    dist_km = round(summary["distance"] / 1000, 2)
            return TravelResult(
                dist_km=dist_km,
                time_walk_min=times.get("walk"),
                time_bike_min=times.get("bike"),
                time_car_min=times.get("car"),
                provider="ors",
            )
        except Exception as exc:  # noqa: BLE001 — cai pro fallback
            logger.warning("ORS falhou (%s); usando estimativa.", exc)
            return None

    def compute(self, lat: float | None, lon: float | None) -> TravelResult | None:
        if lat is None or lon is None:
            return None
        origin = (lat, lon)
        if self._ors is not None:
            result = self._via_ors(origin)
            if result is not None:
                return result
        return _estimate(origin, self.dest)
