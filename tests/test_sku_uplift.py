from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd

from v2.forecasting.festival_calendar import calendar_labels, festival_tags_for_date
from v2.forecasting.sku_uplift import learn_sku_uplift_table, sku_multiplier_for_date
from v2.forecasting.uplift import apply_uplift_to_forecasts


def test_holi_2026_tagged():
    tags = festival_tags_for_date(date(2026, 3, 4))
    assert "in_holi" in tags


def test_july4_weekend_labels():
    labels = calendar_labels(date(2026, 7, 4))  # Saturday
    assert "weekend" in labels
    assert "us_independence" in labels


def test_learn_weekend_uplift_only_for_spiky_sku():
    os.environ["SKU_UPLIFT_ENABLED"] = "1"
    # Build ~6 weeks: baseline weekday 10, weekend 20 for sku A; flat 5 for sku B
    rows = []
    start = date(2026, 1, 5)  # Monday
    for i in range(42):
        d = start + timedelta(days=i)
        wd = d.weekday()
        qty_a = 20.0 if wd >= 5 else 10.0
        qty_b = 5.0
        rows.append({"item_id": "A", "date": d, "quantity": qty_a})
        rows.append({"item_id": "B", "date": d, "quantity": qty_b})
    daily = pd.DataFrame(rows)
    table = learn_sku_uplift_table(daily)
    assert "A" in table and table["A"].get("weekend", 1.0) > 1.0
    assert "B" not in table or table.get("B", {}).get("weekend", 1.0) == 1.0

    m_a, name_a = sku_multiplier_for_date("A", table, as_of=date(2026, 2, 7))  # Sat
    assert m_a > 1.0 and name_a == "sku_weekend"
    m_b, _ = sku_multiplier_for_date("B", table, as_of=date(2026, 2, 7))
    assert m_b == 1.0


def test_apply_uplift_uses_sku_table():
    os.environ["SKU_UPLIFT_ENABLED"] = "1"
    os.environ["UPLIFT_ENABLED"] = "0"
    start = date(2026, 1, 5)
    rows = []
    for i in range(42):
        d = start + timedelta(days=i)
        qty = 18.0 if d.weekday() >= 5 else 9.0
        rows.append({"item_id": "99", "date": d, "quantity": qty})
    daily = pd.DataFrame(rows)
    fc = pd.DataFrame(
        {
            "item_id": ["99"],
            "horizon_days": [7],
            "p50": [10.0],
            "p90": [16.0],
            "demand_class": ["smooth"],
            "model": ["x"],
        }
    )
    out = apply_uplift_to_forecasts(fc, daily=daily, as_of="2026-02-07")
    assert float(out.iloc[0]["uplift_multiplier"]) > 1.0
    assert float(out.iloc[0]["p90"]) > 16.0
    os.environ["SKU_UPLIFT_ENABLED"] = "0"
