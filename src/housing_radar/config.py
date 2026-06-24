"""Configuração central, carregada de variáveis de ambiente (prefixo HR_) e/ou `.env`."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="HR_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Banco ---
    database_url: str = "sqlite:///data/housing_radar.db"

    # --- Destino de referência (campus UFSCar São Carlos) ---
    ufscar_lat: float = -21.9839
    ufscar_lon: float = -47.8807
    destination_label: str = "UFSCar São Carlos"

    # --- Site / metadados (SEO + OpenGraph) ---
    site_base_url: str = "https://ap.v1cferr.dev"
    site_title: str = "UFSCar Housing Radar"
    site_description: str = (
        "Apartamentos à venda perto da UFSCar (São Carlos/SP), coletados, "
        "geolocalizados e ranqueados por proximidade, preço, área e mais."
    )

    # --- Geocoding (Nominatim / OpenStreetMap) ---
    nominatim_user_agent: str = "ufscar-housing-radar/0.1 (contato@v1cferr.dev)"
    # Nominatim exige >= 1s entre requisições. Mantenha folga.
    geocode_rate_seconds: float = 1.1

    # --- Routing opcional (OpenRouteService) ---
    ors_api_key: str | None = None
    ors_base_url: str = "https://api.openrouteservice.org"

    # --- HTTP / coleta ---
    request_timeout: float = 20.0
    collect_max_pages: int = 5
    user_agent: str = (
        "Mozilla/5.0 (compatible; ufscar-housing-radar/0.1; +https://v1cferr.dev)"
    )
    # OBS: a OLX ignora o slug de região nas nossas requisições; o coletor filtra
    # por cidade-alvo (São Carlos) no processamento. Ver collectors/olx.py.
    olx_search_url: str = "https://www.olx.com.br/imoveis/venda/apartamentos/estado-sp"

    # --- Diretórios ---
    export_dir: str = "exports"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
