"""Tests for Okemos news scoping."""

from v2.signals.regional_news import (
    REGION_LABEL,
    clamp_factor,
    match_product_news_factor,
)


def test_news_factor_only_matches_keywords():
    signals = [
        {
            "headline": "Fly disease hits lentils in Okemos 48864",
            "product_keywords": ["lentil", "dal"],
            "demand_factor": 0.85,
            "region_scope": REGION_LABEL,
        }
    ]
    hit = match_product_news_factor(description="TOOR DAL 4LB", signals=signals)
    assert hit["news_factor"] == 0.85
    miss = match_product_news_factor(description="AMUL MILK 1GAL", signals=signals)
    assert miss["news_factor"] == 1.0


def test_clamp_factor():
    assert clamp_factor(0.1) == 0.70
    assert clamp_factor(2.0) == 1.25


def test_forecast_horizon_interpolation():
    from app.dashboard.vendor_reorder_service import _forecast_for_cover

    row = {"forecast_7d": 7.0, "forecast_14d": 14.0, "forecast_30d": 30.0}
    assert abs(_forecast_for_cover(row, 19) - (14 + (30 - 14) * (5 / 16))) < 1e-6
    assert abs(_forecast_for_cover(row, 5) - (7 * 5 / 7)) < 1e-6
