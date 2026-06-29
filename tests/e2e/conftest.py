"""Fixtures dos testes e2e (V1C-68): sobe a API real num DB temporário e dirige o
dashboard via Playwright.

Opt-in: marcados com `e2e` e excluídos do run padrão (ver pyproject). Rode com
`uv run pytest -m e2e` após `uv sync --extra browser && uv run playwright install chromium`.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

# Sem o extra browser instalado, pula a suíte inteira em vez de quebrar a coleta.
sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(db_url: str) -> None:
    """Semeia o DB temporário com as 4 categorias (vários por categoria p/ ranking)."""
    os.environ["HR_DATABASE_URL"] = db_url
    from housing_radar.db import init_db, session_scope
    from housing_radar.models import RawListing
    from housing_radar.pipeline.normalize import normalize
    from housing_radar.pipeline.score import score_listing

    init_db()
    rows = [
        dict(source="manual", source_id="c1", title="Apto 2q Santa Felícia", price=295000,
             area_m2=68, bedrooms=2, neighborhood="Santa Felícia", condo_fee=450,
             transacao="compra", tipo_imovel="apartamento"),
        dict(source="manual", source_id="c2", title="Apto 3q Centro", price=520000,
             area_m2=95, bedrooms=3, neighborhood="Centro", condo_fee=700,
             transacao="compra", tipo_imovel="apartamento"),
        dict(source="manual", source_id="a1", title="Apto p/ alugar Jardim Lutfalla",
             rent_price=1800, area_m2=60, bedrooms=2, neighborhood="Jd Lutfalla",
             condo_fee=400, transacao="aluguel", tipo_imovel="apartamento"),
        dict(source="manual", source_id="k1", title="Kitnet perto da UFSCar", rent_price=950,
             area_m2=28, bedrooms=1, neighborhood="Jd Macarengo",
             transacao="aluguel", tipo_imovel="kitnet"),
        dict(source="manual", source_id="r1", title="Quarto em república mista", rent_price=650,
             area_m2=14, bedrooms=1, neighborhood="Vila Prado",
             transacao="aluguel", tipo_imovel="quarto_republica"),
    ]
    with session_scope() as session:
        for row in rows:
            listing = normalize(RawListing(**row))
            listing.dist_ufscar_km = 2.0
            listing.time_car_min = 8
            score, _ = score_listing(listing)
            listing.score = score
            session.add(listing)
        session.commit()


@pytest.fixture(scope="session")
def live_server(tmp_path_factory) -> str:
    """Sobe o uvicorn num subprocess isolado apontando p/ um SQLite temporário semeado."""
    db_path = tmp_path_factory.mktemp("e2e") / "e2e.db"
    db_url = f"sqlite:///{db_path}"
    _seed(db_url)

    port = _free_port()
    env = {**os.environ, "HR_DATABASE_URL": db_url}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "housing_radar.api.app:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):  # espera o healthz (até ~10s)
            try:
                if urlopen(f"{base}/healthz", timeout=1).status == 200:
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("servidor e2e não respondeu no healthz")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def _browser():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(_browser, live_server):
    """Página já carregada no dashboard, com erros de console capturados em page.console_errors."""
    page = _browser.new_page()
    errors: list[str] = []
    page.console_errors = errors  # type: ignore[attr-defined]
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{live_server}/", wait_until="networkidle")
    page.wait_for_selector(".tabulator-row", timeout=8000)
    yield page
    page.close()
