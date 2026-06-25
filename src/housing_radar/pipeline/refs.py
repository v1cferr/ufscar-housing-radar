"""Referências externas gratuitas: Selic (Banco Central) e CEP (ViaCEP).

Sem chave de API. Cada função é best-effort e tolerante a falha de rede:
retorna None em vez de levantar, para não derrubar o /api/settings.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import httpx

logger = logging.getLogger(__name__)

# Séries do SGS/BCB (https://api.bcb.gov.br/dados/serie/...):
#   432   = Meta Selic definida pelo Copom (% a.a.) — referência macro.
#   20774 = Financiamento imobiliário PF, taxas de mercado (% a.a.) — a taxa real
#           que o comprador costuma pegar; é o melhor ponto de partida para o juros.
_SGS_SELIC = 432
_SGS_FINANC_IMOB = 20774

# Cache simples em processo p/ não bater no BCB a cada page-load.
_RATES_CACHE: dict[str, object] = {"value": None, "fetched_at": None}
_RATES_TTL_SECONDS = 6 * 3600


def _sgs_last(serie: int) -> dict | None:
    """Último ponto de uma série do SGS/BCB: {"valor": float, "data": str} ou None."""
    url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados/ultimos/1?formato=json"
    resp = httpx.get(url, timeout=8, headers={"Accept": "application/json"})
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        return None
    return {
        "valor": float(str(rows[-1]["valor"]).replace(",", ".")),
        "data": str(rows[-1].get("data") or ""),
    }


def fetch_rates() -> dict | None:
    """Selic (meta Copom) + taxa média do financiamento imobiliário PF, do BCB.

    Retorna {"selic", "selic_data", "financiamento", "financiamento_data",
    "juros_sugerido"} — o sugerido é a própria taxa de financiamento (não Selic+spread,
    que superestima). Best-effort, com cache; devolve o cache antigo em caso de falha.
    """
    now = datetime.now(UTC)
    cached = _RATES_CACHE.get("value")
    fetched_at = _RATES_CACHE.get("fetched_at")
    if cached is not None and isinstance(fetched_at, datetime):
        if (now - fetched_at).total_seconds() < _RATES_TTL_SECONDS:
            return cached  # type: ignore[return-value]

    try:
        selic = _sgs_last(_SGS_SELIC)
        financ = _sgs_last(_SGS_FINANC_IMOB)
        if not selic and not financ:
            return cached  # type: ignore[return-value]
        sugerido = financ["valor"] if financ else (selic["valor"] if selic else None)
        result = {
            "selic": selic["valor"] if selic else None,
            "selic_data": selic["data"] if selic else "",
            "financiamento": financ["valor"] if financ else None,
            "financiamento_data": financ["data"] if financ else "",
            "juros_sugerido": round(sugerido, 1) if sugerido is not None else None,
        }
        _RATES_CACHE["value"] = result
        _RATES_CACHE["fetched_at"] = now
        return result
    except Exception as exc:  # rede/parse — devolve o cache antigo se houver
        logger.warning("Falha ao buscar taxas no BCB: %s", exc)
        return cached  # type: ignore[return-value]


def _clean_cep(text: str) -> str | None:
    """Extrai 8 dígitos de um CEP (aceita '13560-000', '13560000', 'cep 13560000')."""
    digits = re.sub(r"\D", "", text or "")
    return digits if len(digits) == 8 else None


def resolve_cep(text: str) -> dict | None:
    """Resolve um CEP em endereço via ViaCEP. Retorna None se não for CEP/achar.

    {"address": "Rua X, Bairro, São Carlos, SP", "localidade": ..., "uf": ...}.
    Funciona mesmo sem número da casa — basta o CEP.
    """
    cep = _clean_cep(text)
    if not cep:
        return None
    try:
        resp = httpx.get(f"https://viacep.com.br/ws/{cep}/json/", timeout=8)
        resp.raise_for_status()
        data = resp.json()
        if not data or data.get("erro"):
            return None
        parts = [
            data.get("logradouro") or "",
            data.get("bairro") or "",
            data.get("localidade") or "",
            data.get("uf") or "",
        ]
        address = ", ".join(p for p in parts if p)
        if not address:
            return None
        return {
            "address": address,
            "bairro": data.get("bairro") or "",
            "localidade": data.get("localidade") or "",
            "uf": data.get("uf") or "",
        }
    except Exception as exc:
        logger.warning("Falha ao consultar ViaCEP (%s): %s", cep, exc)
        return None
