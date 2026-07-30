"""Tests for calendar enrichment."""

from datetime import date

import pandas as pd

from v2.forecasting.calendar_enrichment import enrich_dates, merge_calendar_features


def test_enrich_dates_weekday_and_weekend():
    df = enrich_dates([date(2026, 7, 4), date(2026, 7, 5)])
    assert len(df) == 2
    july4 = df[df["date"] == pd.Timestamp("2026-07-04")].iloc[0]
    assert july4["day_name"] == "Saturday"
    assert bool(july4["is_weekend"]) is True
    assert july4["us_holiday"] == "Independence Day (US)"


def test_indian_festival_flag():
    df = enrich_dates([date(2026, 11, 8)])
    row = df.iloc[0]
    assert row["indian_festival"] == "Diwali"
    assert row["day_type"] == "Indian Festival"


def test_merge_calendar_features():
    sales = pd.DataFrame({"date": [date(2026, 1, 26)], "quantity": [100]})
    out = merge_calendar_features(sales)
    assert "day_name" in out.columns
    assert out.iloc[0]["indian_festival"] == "Republic Day (India)"
