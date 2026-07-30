from __future__ import annotations

import os

import pandas as pd

from v2.forecasting.uplift import apply_uplift_to_forecasts, category_multiplier


def test_uplift_off_by_default():
    os.environ["UPLIFT_ENABLED"] = "0"
    m, name = category_multiplier("beverages", as_of="2026-07-15")
    assert m == 1.0
    assert name is None


def test_uplift_summer_beverages_when_enabled():
    os.environ["UPLIFT_ENABLED"] = "1"
    m, name = category_multiplier("Beverages", as_of="2026-07-15")
    assert m >= 1.25
    assert name is not None
    df = pd.DataFrame(
        {
            "item_id": ["1"],
            "horizon_days": [7],
            "p50": [10.0],
            "p90": [16.0],
            "demand_class": ["smooth"],
            "model": ["x"],
        }
    )
    out = apply_uplift_to_forecasts(df, item_category={"1": "beverages"}, as_of="2026-07-15")
    assert float(out.iloc[0]["p50"]) > 10.0
    os.environ["UPLIFT_ENABLED"] = "0"
