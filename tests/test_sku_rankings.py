"""Tests for SKU ranking weighted score."""

import pandas as pd

from v2.analytics.sku_sales_metrics import rank_skus


def test_weighted_rank_highest_revenue_wins():
    metrics = pd.DataFrame(
        {
            "upc": ["A", "B", "C"],
            "total_revenue": [1000, 100, 10],
            "total_quantity": [500, 50, 5],
            "sales_frequency": [0.9, 0.5, 0.1],
        }
    )
    ranked = rank_skus(metrics)
    assert ranked.loc[ranked["upc"] == "A", "overall_rank"].iloc[0] == 1
    assert ranked.loc[ranked["upc"] == "A", "is_top100"].iloc[0]
