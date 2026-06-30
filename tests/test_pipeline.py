"""Testes do núcleo do pipeline (sem rede)."""

from __future__ import annotations

from housing_radar.models import RawListing
from housing_radar.pipeline.cost import cost_config_from_settings, monthly_cost, parcela_price
from housing_radar.pipeline.normalize import normalize, parse_money
from housing_radar.pipeline.score import score_listing
from housing_radar.pipeline.travel import _estimate


def test_parse_money_ptbr():
    assert parse_money("R$ 350.000,00") == 350000.0
    assert parse_money("1.200") == 1200.0
    assert parse_money(295000) == 295000.0
    assert parse_money(None) is None


def test_normalize_builds_dedupe_key_and_types():
    raw = RawListing(
        source="manual",
        source_id="42",
        title="Apto 2 dorm",
        price="R$ 295.000",
        area_m2="68 m²",
        bedrooms="2",
        neighborhood="Santa Felícia",
    )
    listing = normalize(raw)
    assert listing.price == 295000.0
    assert listing.area_m2 == 68.0
    assert listing.bedrooms == 2
    assert listing.dedupe_key == "manual:42"


def test_travel_estimate_increases_with_distance():
    near = _estimate((-21.9905, -47.8730), (-21.9839, -47.8807))
    far = _estimate((-22.0170, -47.8900), (-21.9839, -47.8807))
    assert far.dist_km > near.dist_km
    assert far.time_car_min > near.time_car_min
    assert near.provider == "estimate"


def test_score_rewards_proximity_and_price():
    cheap_near = normalize(
        RawListing(source="m", source_id="1", price=250000, area_m2=80, bedrooms=2)
    )
    cheap_near.dist_ufscar_km = 1.5
    expensive_far = normalize(
        RawListing(source="m", source_id="2", price=600000, area_m2=80, bedrooms=2)
    )
    expensive_far.dist_ufscar_km = 8.0

    s1, _ = score_listing(cheap_near)
    s2, _ = score_listing(expensive_far)
    assert s1 > s2


def test_msys_sitemap_filters_sale_only():
    from housing_radar.collectors.msys import _SC_APT_DETAIL as rx

    base = "https://x.com.br/imovel"
    # venda e venda-e-locacao entram; locacao (aluguel puro) fica de fora.
    assert rx.search(f"{base}/venda/apartamentos/sao-carlos/centro-ed-foo/123").group(1) == "123"
    assert rx.search(f"{base}/venda-e-locacao/apartamentos/sao-carlos/bar/456").group(1) == "456"
    assert rx.search(f"{base}/locacao/apartamentos/sao-carlos/baz/789") is None
    # outras cidades / categorias não entram
    assert rx.search(f"{base}/venda/casas/sao-carlos/qux/1") is None
    assert rx.search(f"{base}/venda/apartamentos/campinas/quux/2") is None


def test_score_penalizes_implausible_price():
    # R$ 3.200 "à venda" é quase certamente aluguel/erro -> subscore de preço 0.
    suspect = normalize(RawListing(source="m", source_id="r", price=3200, bedrooms=2))
    _, breakdown = score_listing(suspect)
    assert breakdown["subscores"]["price"] == 0.0


def test_score_handles_missing_data():
    listing = normalize(RawListing(source="m", source_id="x", price=300000))
    score, breakdown = score_listing(listing)
    assert score is not None
    assert "price" in breakdown["subscores"]


def test_score_value_bonus_for_low_price_to_rent():
    # Mesmo imóvel; o que também informa aluguel (bom custo-benefício) pontua mais.
    base = normalize(RawListing(source="m", source_id="a", price=200000, area_m2=70, bedrooms=2))
    base.dist_ufscar_km = 2.0
    with_rent = normalize(
        RawListing(source="m", source_id="b", price=200000, rent_price=2000, area_m2=70, bedrooms=2)
    )
    with_rent.dist_ufscar_km = 2.0

    s_base, _ = score_listing(base)
    s_rent, breakdown = score_listing(with_rent)
    assert "value" in breakdown["subscores"]
    assert s_rent > s_base


def test_score_penalizes_unknown_location():
    # Sem geocoding (dist None) deve pontuar abaixo do mesmo imóvel localizado e perto.
    base = RawListing(source="m", source_id="a", price=200000, area_m2=70, bedrooms=2)
    located = normalize(base.model_copy(update={"source_id": "b"}))
    located.dist_ufscar_km = 1.5
    unlocated = normalize(base)  # dist fica None

    s_located, _ = score_listing(located)
    s_unlocated, breakdown = score_listing(unlocated)
    assert "proximity" in breakdown["subscores"]  # presente, mas penalizada
    assert s_unlocated < s_located
    assert s_unlocated < 80  # não dispara para o topo


def test_score_aluguel_usa_perfil_proprio_nao_o_piso_de_venda():
    # Regressão (V1C-68): um aluguel de R$1500 NÃO pode cair no price_floor de
    # venda e zerar — ele usa o perfil de aluguel (critério "rent").
    rental = normalize(
        RawListing(
            source="m", source_id="al1", transacao="aluguel",
            rent_price=1500, area_m2=55, bedrooms=2,
        )
    )
    rental.dist_ufscar_km = 2.0
    score, breakdown = score_listing(rental)
    assert score is not None and score > 40
    assert "rent" in breakdown["subscores"]
    assert "price" not in breakdown["subscores"]  # nada de critério de venda
    assert "value" not in breakdown["subscores"]  # bônus preço/aluguel é só de compra


def test_score_aluguel_mais_barato_pontua_mais():
    cheap = normalize(
        RawListing(source="m", source_id="c", transacao="aluguel",
                   rent_price=900, area_m2=55, bedrooms=2)
    )
    cheap.dist_ufscar_km = 2.0
    pricey = normalize(
        RawListing(source="m", source_id="p", transacao="aluguel",
                   rent_price=2500, area_m2=55, bedrooms=2)
    )
    pricey.dist_ufscar_km = 2.0
    s_cheap, _ = score_listing(cheap)
    s_pricey, _ = score_listing(pricey)
    assert s_cheap > s_pricey


def test_score_compra_default_usa_perfil_de_venda():
    sale = normalize(RawListing(source="m", source_id="s", price=300000, area_m2=70, bedrooms=2))
    assert sale.transacao == "compra"  # default do normalize
    _, breakdown = score_listing(sale)
    assert "price" in breakdown["subscores"]
    assert "rent" not in breakdown["subscores"]


def test_parcela_price_espelha_tabela_price():
    # 300k, entrada 20% (financia 240k), 11% a.a., 360 meses -> ~R$2.2k/mês.
    p = parcela_price(300_000, entrada_pct=20, juros_aa=11, prazo_meses=360)
    assert p is not None and 2000 < p < 2400
    # Mais entrada -> parcela menor; prazo/preço inválidos -> None.
    assert parcela_price(300_000, 50, 11, 360) < p
    assert parcela_price(None, 20, 11, 360) is None
    assert parcela_price(300_000, 20, 11, 0) is None


def test_monthly_cost_compra_soma_parcela_iptu_condo_contas():
    sale = normalize(RawListing(source="m", source_id="s", price=300_000, condo_fee=500))
    total, bd = monthly_cost(sale)
    assert "parcela" in bd["parts"] and "iptu" in bd["parts"]
    assert bd["parts"]["condominio"] == 500 and bd["parts"]["contas"] == 250
    assert total == round(sum(bd["parts"].values()), 2)
    assert total > bd["parts"]["parcela"]  # soma > só a parcela


def test_monthly_cost_aluguel_usa_aluguel_nao_parcela():
    rental = normalize(
        RawListing(source="m", source_id="al", transacao="aluguel", rent_price=1500, condo_fee=400)
    )
    total, bd = monthly_cost(rental)
    assert bd["parts"]["aluguel"] == 1500
    assert "parcela" not in bd["parts"]  # aluguel não financia
    assert total == 1500 + 400 + 250  # aluguel + condomínio + contas (IPTU aluguel = 0 default)


def test_monthly_cost_republica_e_all_in_sem_contas():
    # Repúblicas: o aluguel já inclui água/luz/internet -> não soma contas estimadas.
    rep = normalize(
        RawListing(source="m", source_id="r", transacao="aluguel",
                   tipo_imovel="quarto_republica", rent_price=550)
    )
    total, bd = monthly_cost(rep)
    assert bd["parts"]["aluguel"] == 550
    assert "contas" not in bd["parts"]  # all-in
    assert total == 550


def test_monthly_cost_sem_base_retorna_none():
    # Compra sem preço: não há parcela nem aluguel -> sem custo estimável.
    vazio = normalize(RawListing(source="m", source_id="v", condo_fee=300))
    total, bd = monthly_cost(vazio)
    assert total is None and "note" in bd


def test_cost_config_from_settings_le_financiamento():
    cfg = cost_config_from_settings(
        {"entrada_pct": "30", "juros_aa": "9.5", "prazo_meses": "240",
         "iptu_aa": "1.2", "contas": "300"}
    )
    assert cfg.entrada_pct == 30.0 and cfg.juros_aa == 9.5 and cfg.prazo_meses == 240
    assert cfg.iptu_aa_pct == 1.2 and cfg.contas_mensal == 300.0
    # Vazio/ausente -> mantém defaults.
    assert cost_config_from_settings({}).entrada_pct == 20.0


def test_apply_aba_mapeia_transacao_e_tipo():
    from sqlmodel import select

    from housing_radar.api.app import _apply_aba
    from housing_radar.models import Listing

    base = select(Listing)
    assert _apply_aba(base, None).whereclause is None  # sem aba -> sem filtro
    for aba in ("compra", "aluguel", "kitnet", "republica"):
        assert _apply_aba(base, aba).whereclause is not None
    al = str(_apply_aba(base, "aluguel").whereclause)
    assert "transacao" in al and "tipo_imovel" in al
    assert "tipo_imovel" in str(_apply_aba(base, "kitnet").whereclause)
    assert "transacao" in str(_apply_aba(base, "compra").whereclause)


def test_grupozap_parses_jsonld_apartment():
    from housing_radar.collectors.grupozap import GrupoZapCollector

    apt = {
        "@type": "Apartment",
        "name": "Apartamento para comprar com 48 m², 2 quartos, 1 banheiro, 1 vaga em  Recreio São Judas Tadeu, São Carlos",
        "url": "https://www.vivareal.com.br/imovel/foo-id-2893995129/",
        "numberOfBedrooms": 2,
        "numberOfBathroomsTotal": 1,
        "floorSize": {"value": 48, "unitCode": "M2"},
        "address": {"streetAddress": "Avenida Gregório Aversa", "addressLocality": "São Carlos"},
        "offers": {"url": "https://www.vivareal.com.br/imovel/venda-RS253000-id-2893995129/", "price": 253000},
    }
    raw = GrupoZapCollector("vivareal")._parse(apt)
    assert raw is not None
    assert raw.source == "vivareal"
    assert raw.source_id == "2893995129"
    assert raw.price == 253000
    assert raw.bedrooms == 2
    assert raw.parking_spots == 1
    assert raw.neighborhood == "Recreio São Judas Tadeu"


def test_grupozap_aluguel_roteia_preco_para_rent():
    from housing_radar.collectors.grupozap import GrupoZapCollector

    apt = {
        "@type": "Apartment",
        "name": "Apartamento para alugar com 45 m², 2 quartos, 1 banheiro em Parque Fehr, São Carlos",
        "url": "https://www.vivareal.com.br/imovel/foo-id-999/",
        "numberOfBedrooms": 2,
        "floorSize": {"value": 45},
        "address": {"addressLocality": "São Carlos"},
        "offers": {"url": "https://www.vivareal.com.br/imovel/aluguel-id-999/", "price": 889},
    }
    raw = GrupoZapCollector("vivareal", "aluguel")._parse(apt)
    assert raw is not None
    assert raw.transacao == "aluguel" and raw.tipo_imovel == "apartamento"
    assert raw.rent_price == 889 and raw.price is None  # aluguel não vai pra price de venda
    assert raw.neighborhood == "Parque Fehr"


def test_collapse_duplicates_groups_across_sources():
    from housing_radar.api.app import _SORTS, _collapse_duplicates

    def mk(i, src, price, score):
        listing = normalize(
            RawListing(source=src, source_id=str(i), price=price, area_m2=70,
                       bedrooms=2, neighborhood="Centro")
        )
        listing.id = i
        listing.score = score
        return listing

    a = mk(1, "vivareal", 200000, 80)
    b = mk(2, "zap", 200000, 78)         # mesma assinatura de 'a' -> agrupa
    c = mk(3, "cardinali", 350000, 90)   # preço diferente -> grupo próprio

    reps, meta = _collapse_duplicates([a, b, c], _SORTS["score"])
    rep_ids = {r.id for r in reps}
    assert len(reps) == 2
    assert 3 in rep_ids                  # c fica sozinho
    assert 1 in rep_ids and 2 not in rep_ids  # grupo colapsa no melhor score
    assert meta[1]["count"] == 2
    assert set(meta[1]["sources"]) == {"vivareal", "zap"}


def test_parse_rep_price_lida_com_faixas_e_simbolos():
    from housing_radar.collectors.reps_sanca import parse_rep_price

    assert parse_rep_price("R$ 450,00") == 450.0
    assert parse_rep_price("~R$ 680") == 680.0
    assert parse_rep_price("600-650") == 625.0  # faixa -> ponto médio
    assert parse_rep_price("R$ 700-750") == 725.0
    assert parse_rep_price("$450") == 450.0
    assert parse_rep_price("") is None
    assert parse_rep_price(None) is None


_REPS_MD = (
    "| Area 51 | Coluna 1 | Coluna 2 | Coluna 3 | Coluna 4 | Coluna 5 | Coluna 6 |\n"
    "| :-: | :-: | :-: | :-: | :-: | :-: | :-: |\n"
    "| Voodoo | 0 | 9 atualmente | R$ 450,00 | 18 min da UFSCar | Calado: 1199 | @repvoodoo |\n"
    "| Lótus | 2 | 11 | 600-650 | 5 min da USP | Lana: 1198 | @replotus |\n"
    "\n"
    "| Nome | nº de vagas | nº de moradoras | Preço médio | Referência | Contato | Insta |\n"
    "| :-: | :-: | :-: | :-: | :-: | :-: | :-: |\n"
    "| LÓTUS | 1 | 10 | R$ 500,00 | perto | Cilada: 4399 | @republicalotus |\n"
    "| Aruêra |  |  |  |  |  |  |\n"
    "\n"
    "| Nome | nº de vagas | nº de moradores | Preço médio | Referência | Contato | Insta |\n"
    "| :-: | :-: | :-: | :-: | :-: | :-: | :-: |\n"
    "| Error 404 | 1 | 4 | R$ 600,00 | rodoviária | Daisy | @error404 |\n"
)


def test_reps_sanca_exclui_femininas_por_padrao(tmp_path):
    from housing_radar.collectors.reps_sanca import RepsSancaCollector

    md = tmp_path / "reps.md"
    md.write_text(_REPS_MD, encoding="utf-8")
    raws = RepsSancaCollector(md).collect()
    by_title = {r.title: r for r in raws}

    # T1 (Area 51 = mista) e T3 ('moradores' = masculina) entram;
    # T2 ('moradoras' = feminina) fica de fora por padrão.
    assert set(by_title) == {"República Voodoo", "República Lótus", "República Error 404"}
    assert "República LÓTUS" not in by_title and "República Aruêra" not in by_title
    # gênero anotado na descrição e no raw
    assert by_title["República Voodoo"].description.startswith("República mista")
    assert by_title["República Error 404"].raw["genero"] == "masculina"
    assert all(r.raw["genero"] != "feminina" for r in raws)
    # categoria/atributos e parsing de preço (simples + faixa)
    assert all(r.transacao == "aluguel" and r.tipo_imovel == "quarto_republica" for r in raws)
    assert by_title["República Voodoo"].rent_price == 450.0
    assert by_title["República Lótus"].rent_price == 625.0
    assert by_title["República Voodoo"].url == "https://instagram.com/repvoodoo"
    assert "atualmente" not in by_title["República Voodoo"].description


def test_reps_sanca_incluir_feminina_resolve_colisao_de_slug(tmp_path):
    from housing_radar.collectors.reps_sanca import RepsSancaCollector

    md = tmp_path / "reps.md"
    md.write_text(_REPS_MD, encoding="utf-8")
    raws = RepsSancaCollector(md, incluir_feminina=True).collect()

    titles = {r.title for r in raws}
    assert "República LÓTUS" in titles  # feminina entra com a flag
    assert "República Aruêra" in titles  # lead sem preço entra
    # Lótus (mista) e LÓTUS (feminina) colidem no slug -> source_ids únicos
    ids = [r.source_id for r in raws]
    assert len(set(ids)) == len(ids)
    assert "lotus" in ids and "lotus-2" in ids


def test_cardinali_aluguel_roteia_preco_para_rent():
    from bs4 import BeautifulSoup

    from housing_radar.collectors.cardinali import _parse_card

    html = """
    <div class="muda_card1">
      <div class="cod-imovel"><strong>239433</strong></div>
      <a class="carousel-cell" href="/imovel/239433"></a>
      <div class="card-titulo">Apartamento - Padrão</div>
      <div class="card-valores">R$ 1.550,00 L</div>
      <div class="imo-dad-compl">2 Dorm. 1 Banho 1 Garagem 60.00 m² A. Útil</div>
      <div class="card-bairro-cidade-texto">Residencial Parati - São Carlos/SP</div>
    </div>
    """
    card = BeautifulSoup(html, "lxml").select_one(".muda_card1")
    rl = _parse_card(card, transacao="aluguel", tipo_imovel="apartamento")
    assert rl is not None
    assert rl.transacao == "aluguel" and rl.tipo_imovel == "apartamento"
    assert rl.rent_price == "R$ 1.550,00 L" and rl.price is None  # aluguel não vira preço de venda
    assert rl.source_id == "239433" and rl.bedrooms == 2


def test_grupozap_kitnet_parseia_product():
    from housing_radar.collectors.grupozap import GrupoZapCollector

    # A página de kitnet do VivaReal usa @type "Product" (mesmos campos do Apartment).
    prod = {
        "@type": "Product",
        "name": "Kitnet/Conjugado para alugar com 35 m², 1 quarto em Vila Brasília, São Carlos",
        "numberOfBedrooms": 1,
        "floorSize": {"value": 35, "unitCode": "M2"},
        "address": {"streetAddress": "Rua X", "addressLocality": "São Carlos"},
        "offers": {"url": "https://www.vivareal.com.br/imovel/kitnet-aluguel-RS480-id-555/", "price": 480},
    }
    col = GrupoZapCollector("vivareal", "aluguel", "kitnet")
    assert col.jsonld_type == "Product" and "kitnet_residencial" in col.search_url
    raw = col._parse(prod)
    assert raw is not None
    assert raw.transacao == "aluguel" and raw.tipo_imovel == "kitnet"
    assert raw.rent_price == 480 and raw.price is None
    assert raw.source_id == "555" and raw.bedrooms == 1 and raw.area_m2 == 35
    assert raw.neighborhood == "Vila Brasília"


def test_grupozap_skips_other_cities():
    from housing_radar.collectors.grupozap import GrupoZapCollector

    apt = {
        "@type": "Apartment",
        "name": "Apartamento em Campinas",
        "offers": {"url": "https://www.zapimoveis.com.br/imovel/x-id-99/", "price": 100000},
        "address": {"addressLocality": "Campinas"},
    }
    assert GrupoZapCollector("zap")._parse(apt) is None
