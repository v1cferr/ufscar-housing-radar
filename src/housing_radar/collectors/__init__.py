"""Registro de coletores disponíveis."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from housing_radar.collectors.base import Collector
from housing_radar.collectors.cardinali import CardinaliCollector
from housing_radar.collectors.grupozap import GRUPOZAP_SITES, GrupoZapCollector, make_grupozap
from housing_radar.collectors.imovelweb import ImovelWebCollector
from housing_radar.collectors.manual import ManualCSVCollector
from housing_radar.collectors.msys import MSYS_SITES, MSYSCollector, make_msys
from housing_radar.collectors.olx import OLXCollector

# Coletores que dependem de browser (Playwright) — pesados e opcionais. Ficam fora
# do `collect all` (precisam do extra `browser` + Chromium e não entram no Docker).
BROWSER_COLLECTORS: set[str] = {"imovelweb"}

# Coletores remotos acionáveis pela CLI por nome -> factory sem argumentos.
# (O manual exige um caminho de CSV, então é tratado à parte via import-csv.)
REMOTE_COLLECTORS: dict[str, Callable[[], Collector]] = {
    "cardinali": CardinaliCollector,
    "olx": OLXCollector,
    **{name: partial(make_msys, name) for name in MSYS_SITES},
    **{name: partial(make_grupozap, name) for name in GRUPOZAP_SITES},
    "imovelweb": ImovelWebCollector,
}

__all__ = [
    "Collector",
    "ManualCSVCollector",
    "OLXCollector",
    "CardinaliCollector",
    "MSYSCollector",
    "GrupoZapCollector",
    "ImovelWebCollector",
    "REMOTE_COLLECTORS",
    "BROWSER_COLLECTORS",
]
