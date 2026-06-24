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
