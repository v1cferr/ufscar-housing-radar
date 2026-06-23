"""Geocoding de endereços via Nominatim (OpenStreetMap), com rate limit e cache."""

from __future__ import annotations

import logging

from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

from housing_radar.config import get_settings

logger = logging.getLogger(__name__)


class Geocoder:
    def __init__(self) -> None:
        settings = get_settings()
        self._geocode_raw = Nominatim(user_agent=settings.nominatim_user_agent)
        # RateLimiter respeita a política do Nominatim (>= 1 req/s).
        self._geocode = RateLimiter(
            self._geocode_raw.geocode,
            min_delay_seconds=settings.geocode_rate_seconds,
            max_retries=2,
            swallow_exceptions=True,
        )
        self._cache: dict[str, tuple[float, float] | None] = {}

    def geocode(self, address: str | None) -> tuple[float, float] | None:
        if not address:
            return None
        if address in self._cache:
            return self._cache[address]

        # Foca a busca em São Carlos/SP para reduzir ambiguidade.
        query = address if "são carlos" in address.lower() else f"{address}, São Carlos, SP, Brasil"
        location = self._geocode(query, country_codes="br")
        result = (location.latitude, location.longitude) if location else None
        if result is None:
            logger.warning("Geocoding falhou para: %s", address)
        self._cache[address] = result
        return result
