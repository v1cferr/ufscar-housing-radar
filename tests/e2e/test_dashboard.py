"""E2e do dashboard/funil (V1C-68): exercita o que o unit test não alcança — abas,
ranking Geral, relabel de filtro, custo mensal e round-trip da estratégia.

Opt-in: `uv run pytest -m e2e` (ver tests/e2e/conftest.py).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _visible_rows(page):
    return [r for r in page.query_selector_all(".tabulator-row") if r.is_visible()]


def _cats(rows):
    return {r.query_selector(".cat").inner_text().strip() for r in rows if r.query_selector(".cat")}


def _click_aba(page, aba):
    page.click(f'.aba[data-aba="{aba}"]')
    page.wait_for_timeout(350)


def test_aba_geral_e_default_e_lista_todas_categorias(page):
    geral = page.query_selector('.aba[data-aba="geral"]')
    assert geral, "aba Geral deve existir"
    assert "on" in (geral.get_attribute("class") or ""), "Geral deve estar ativa por padrão"
    assert _cats(_visible_rows(page)) == {"Compra", "Aluguel", "Kitnet", "República"}
    assert page.console_errors == []


def test_aba_geral_ordena_por_score_desc(page):
    def score_of(row):
        el = row.query_selector('[tabulator-field="score"]')
        try:
            return float((el.inner_text() if el else "").strip().replace(",", "."))
        except ValueError:
            return -1.0

    scores = [score_of(r) for r in _visible_rows(page)]
    assert scores == sorted(scores, reverse=True), f"ranking não está em score desc: {scores}"


def test_aba_compra_filtra_so_compra_e_relabela_preco(page):
    _click_aba(page, "compra")
    assert _cats(_visible_rows(page)) == {"Compra"}
    assert "Preço" in page.inner_text("label[for=max_price]")


def test_aba_kitnet_filtra_so_kitnet(page):
    _click_aba(page, "kitnet")
    assert _cats(_visible_rows(page)) == {"Kitnet"}


def test_rotulo_valor_max_na_aba_geral(page):
    assert "Valor" in page.inner_text("label[for=max_price]")
    _click_aba(page, "aluguel")
    assert "Aluguel" in page.inner_text("label[for=max_price]")


def test_aba_republica_ordena_por_preco_asc(page):
    # Repúblicas não têm distância/score útil -> a aba ranqueia por preço (mais barata no topo).
    _click_aba(page, "republica")
    rows = _visible_rows(page)
    assert _cats(rows) == {"República"}

    def rent_of(row):
        el = row.query_selector('[tabulator-field="rent_price"]')
        nums = "".join(ch for ch in (el.inner_text() if el else "") if ch.isdigit())
        return int(nums) if nums else 10**9

    rents = [rent_of(r) for r in rows]
    assert rents == sorted(rents), f"República não está em preço asc: {rents}"


def test_coluna_custo_mensal_bate_com_cost_py(page):
    # Aluguel R$1800 + condomínio 400 + contas 250 (default), IPTU aluguel = 0 -> 2450.
    _click_aba(page, "aluguel")
    rows = _visible_rows(page)
    apto = next(r for r in rows if "Lutfalla" in r.inner_text())
    custo = apto.query_selector('[tabulator-field="_custo"]').inner_text()
    assert "2.450" in custo, f"custo mensal do aluguel inesperado: {custo!r}"


def test_classificar_estrategia_persiste(page, live_server):
    import json
    from urllib.request import urlopen

    # Abre o 1º imóvel, classifica como "Ponte" e confirma o chip na grade.
    page.query_selector(".tabulator-row").click()
    page.wait_for_selector("#modal:not([hidden])", timeout=4000)
    page.click('#m-estr button[data-estr="ponte"]')
    page.wait_for_timeout(400)
    page.keyboard.press("Escape")

    # Persistiu no backend? (o POST de estratégia grava no DB)
    data = json.loads(urlopen(f"{live_server}/api/listings?limit=5000").read())
    assert any(d.get("estrategia") == "ponte" for d in data), "estratégia não persistiu no backend"
    assert page.console_errors == []
