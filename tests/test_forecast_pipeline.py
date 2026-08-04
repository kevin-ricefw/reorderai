"""Phase-1 classification + Croston/TSB + Monte Carlo P50/P90."""

from __future__ import annotations

import numpy as np
import pandas as pd

from v2.forecasting.croston import fit_croston_sba, fit_tsb
from v2.forecasting.pipeline import build_forecast_store_frame, forecast_item
from v2.forecasting.syntetos_boylan import classify_demand_series


def test_classify_smoothish_series():
    # Demand almost every day, stable size
    y = pd.Series([5.0] * 40)
    out = classify_demand_series(y)
    assert out["demand_class"] == "smooth"
    assert out["nonzero_days"] == 40


def test_classify_intermittent_series():
    # Sale every ~5 days, constant size → intermittent
    y = pd.Series([0.0] * 50)
    for i in range(0, 50, 5):
        y.iloc[i] = 10.0
    out = classify_demand_series(y)
    assert out["demand_class"] == "intermittent"


def test_classify_single_demand_day():
    y = pd.Series([0.0] * 20)
    y.iloc[3] = 8.0
    out = classify_demand_series(y)
    assert out["demand_class"] == "single_demand_day"


def test_rule_based_one_day_series_not_daily_certainty():
    """A single historical sale must not become p=1.0 (sell every day)."""
    from v2.forecasting.croston import fit_rule_based

    y = pd.Series([10.0], index=pd.to_datetime(["2026-01-07"]))
    p = fit_rule_based(y)
    assert p.model == "rule"
    assert p.demand_probability <= 1.0 / 90 + 1e-9
    assert p.expected_daily <= 10.0 / 90 + 1e-9


def test_single_demand_day_forecast_not_inflated():
    """One sale of 10 in a long catalog span → P50 over 14d stays small."""
    start = pd.Timestamp("2026-01-01")
    rows = [{"item_id": "189", "date": start + pd.Timedelta(days=d), "quantity": 0.0} for d in range(120)]
    rows[6] = {"item_id": "189", "date": start + pd.Timedelta(days=6), "quantity": 10.0}
    # Companion SKU stretches the catalog calendar (same as multi-SKU POS dump)
    rows += [
        {"item_id": "other", "date": start + pd.Timedelta(days=d), "quantity": 1.0}
        for d in range(120)
    ]
    daily = pd.DataFrame(rows)
    classifications, forecasts = build_forecast_store_frame(daily, horizons=[14])
    row = forecasts[(forecasts["item_id"] == "189") & (forecasts["horizon_days"] == 14)].iloc[0]
    assert str(row["demand_class"]) == "single_demand_day"
    # Must NOT look like ~10 units/day × 14 ≈ 140
    assert float(row["p50"]) < 20.0


def test_croston_sba_positive_daily():
    y = pd.Series([0.0, 0.0, 10.0, 0.0, 0.0, 12.0, 0.0, 0.0, 9.0, 0.0])
    p = fit_croston_sba(y)
    assert p.model == "sba"
    assert p.expected_daily > 0
    assert 0 < p.demand_probability <= 1


def test_tsb_handles_long_zeros():
    y = pd.Series([10.0] + [0.0] * 30 + [8.0])
    p = fit_tsb(y)
    assert p.model == "tsb"
    assert p.expected_daily >= 0


def test_pipeline_writes_standard_horizons():
    rng = np.random.default_rng(0)
    rows = []
    start = pd.Timestamp("2026-01-01")
    for d in range(60):
        qty = float(rng.integers(0, 3))
        if d % 4 == 0:
            qty = 6.0
        rows.append({"item_id": "101", "date": start + pd.Timedelta(days=d), "quantity": qty})
    daily = pd.DataFrame(rows)
    classifications, forecasts = build_forecast_store_frame(daily)
    assert len(classifications) == 1
    assert set(forecasts["horizon_days"]) == {7, 14, 21, 30, 45}
    assert (forecasts["p90"] >= forecasts["p50"]).all()


def test_forecast_item_intermittent_mc():
    y = pd.Series([0.0, 10.0, 0.0, 0.0, 12.0, 0.0, 0.0, 9.0] * 5)
    rows = forecast_item(y, demand_class="intermittent", horizons=[7, 14], item_id="x")
    assert len(rows) == 2
    assert rows[0]["model"] == "croston_sba"
    assert rows[0]["p90"] >= rows[0]["p50"]
