"""Tests for exact POS 30-day reorder formula."""

import pandas as pd

from app.dashboard.pos_reorder_math import compute_pos_ai_min


def test_compute_pos_ai_min_coca_cola_style():
    # ADS=20/day for 30 days, lead=7, low variance
    dates = pd.date_range("2026-06-01", periods=30, freq="D")
    daily = pd.DataFrame({"date": dates, "quantity": [20.0] * 30})
    result = compute_pos_ai_min(daily, lead_time_days=7, ads_window_days=30)
    assert result["ads"] == 20.0
    assert result["lead_time_demand"] == 140
    assert result["ai_min"] >= 140


def test_empty_sales():
    result = compute_pos_ai_min(pd.DataFrame(columns=["date", "quantity"]), lead_time_days=7)
    assert result["ai_min"] == 0


def test_negative_stock_extra_sold_boosts_ads():
    # −45 on hand → 45 extra sold into ADS
    dates = pd.date_range("2026-06-01", periods=30, freq="D")
    daily = pd.DataFrame({"date": dates, "quantity": [1.0] * 30})  # 30 sold
    base = compute_pos_ai_min(daily, lead_time_days=7, ads_window_days=30)
    boosted = compute_pos_ai_min(
        daily, lead_time_days=7, ads_window_days=30, extra_sold_units=45
    )
    assert boosted["total_sold_30d"] == 75
    assert boosted["ads"] == 2.5
    assert boosted["extra_sold_from_negative_stock"] == 45
    assert boosted["ai_min"] >= base["ai_min"]


def test_intermittent_sales_do_not_inflate_safety_for_cover():
    """Sparse burst days must not turn 15-day cover into weeks of over-order."""
    # 36 units across 4 burst days inside the lookback; rest of month is zero.
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2026-06-25", "2026-07-02", "2026-07-10", "2026-07-18"]
            ),
            "quantity": [24.0, 1.0, 6.0, 5.0],
        }
    )
    result = compute_pos_ai_min(
        daily,
        lead_time_days=15,
        ads_window_days=30,
        as_of_date="2026-07-21",
    )
    assert result["ads"] == 1.2
    assert result["lead_time_demand"] == 18
    # Safety cannot exceed lead-time demand (old sparse-std path was ~65).
    assert result["safety_stock"] <= result["lead_time_demand"]
    assert result["ai_min"] <= 40  # ~18 LTD + <=18 SS


def test_as_of_includes_recent_zero_days():
    """Lookback ends on store as-of, not the SKU's last sale date."""
    daily = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-01", "2026-06-10"]),
            "quantity": [30.0, 30.0],
        }
    )
    # If window ended at last sale (6/10), sold=60. With as_of 7/21 the
    # lookback is 6/22–7/21, so both sales are outside → ADS 0.
    result = compute_pos_ai_min(
        daily,
        lead_time_days=15,
        ads_window_days=30,
        as_of_date="2026-07-21",
    )
    assert result["total_sold_30d"] == 0
    assert result["ads"] == 0.0
    assert result["ai_min"] == 0
