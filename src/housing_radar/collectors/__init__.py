"""Registro de coletores disponíveis."""

from __future__ import annotations

from housing_radar.collectors.base import Collector
from housing_radar.collectors.manual import ManualCSVCollector
from housing_radar.collectors.olx import OLXCollector

# Coletores "remotos" (sem argumentos obrigatórios) acionáveis pela CLI por nome.
# O manual exige um caminho de CSV, então é tratado à parte.
REMOTE_COLLECTORS: dict[str, type[Collector]] = {
    "olx": OLXCollector,
}

__all__ = ["Collector", "ManualCSVCollector", "OLXCollector", "REMOTE_COLLECTORS"]
