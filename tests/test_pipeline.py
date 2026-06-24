"""Testes do núcleo do pipeline (sem rede)."""

from __future__ import annotations

from housing_radar.models import RawListing
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


def test_grupozap_skips_other_cities():
    from housing_radar.collectors.grupozap import GrupoZapCollector

    apt = {
        "@type": "Apartment",
        "name": "Apartamento em Campinas",
        "offers": {"url": "https://www.zapimoveis.com.br/imovel/x-id-99/", "price": 100000},
        "address": {"addressLocality": "Campinas"},
    }
    assert GrupoZapCollector("zap")._parse(apt) is None
