from datetime import date
from pathlib import Path

from v2.forecasting.local_pos_sales import (
    calendar_features,
    local_sales_to_demand,
    parse_sale_date_from_filename,
)


def test_parse_sale_date_from_filename():
    assert parse_sale_date_from_filename(Path("Product Sales APRIL 1.csv")) == date(
        2026, 4, 1
    )
    assert parse_sale_date_from_filename(Path("Product Sales MAY 12.csv")) == date(
        2026, 5, 12
    )
    assert parse_sale_date_from_filename(Path("Product Sales JAN 3.csv")) == date(
        2026, 1, 3
    )
    assert parse_sale_date_from_filename(Path("Product Sales FEB 14.csv")) == date(
        2026, 2, 14
    )
    assert parse_sale_date_from_filename(Path("Product Sales.csv")) is None


def test_calendar_weekend_flag():
    import pandas as pd

    daily = local_sales_to_demand(
        pd.DataFrame(
            {
                "upc": ["1", "1"],
                "sale_date": [date(2026, 4, 4), date(2026, 4, 5)],  # Sat, Sun
                "quantity": [3.0, 5.0],
                "description": ["a", "a"],
                "net_sales": [1.0, 2.0],
                "source_file": ["x", "y"],
            }
        )
    )
    cal = calendar_features(daily)
    assert bool(cal.iloc[0]["is_weekend"]) is True
    assert cal.iloc[0]["weekday"] == "Saturday"
