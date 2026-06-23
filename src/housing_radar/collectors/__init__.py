"""Registro de coletores disponíveis."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from housing_radar.collectors.base import Collector
from housing_radar.collectors.cardinali import CardinaliCollector
from housing_radar.collectors.manual import ManualCSVCollector
from housing_radar.collectors.msys import MSYS_SITES, MSYSCollector, make_msys
from housing_radar.collectors.olx import OLXCollector

# Coletores remotos acionáveis pela CLI por nome -> factory sem argumentos.
# (O manual exige um caminho de CSV, então é tratado à parte via import-csv.)
REMOTE_COLLECTORS: dict[str, Callable[[], Collector]] = {
    "cardinali": CardinaliCollector,
    "olx": OLXCollector,
    **{name: partial(make_msys, name) for name in MSYS_SITES},
}

__all__ = [
    "Collector",
    "ManualCSVCollector",
    "OLXCollector",
    "CardinaliCollector",
    "MSYSCollector",
    "REMOTE_COLLECTORS",
]
