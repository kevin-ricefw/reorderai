"""Tests for global product search."""

import pandas as pd

from app.dashboard.product_search_service import search_products, vendors_for_query


def test_search_finds_lays_vendor():
    index = pd.DataFrame(
        [
            {
                "product_name": "LAY'S CLASSIC 7OZ",
                "search_text": "lays classic 7oz",
                "vendor_name": "PREMIER FOODS",
                "vendor_key": "PREMIER",
                "upc": "123",
                "source": "Inventory (POS)",
                "unit_cost": 2.5,
                "on_hand": 10,
            },
            {
                "product_name": "RICE 20LB",
                "search_text": "rice 20lb",
                "vendor_name": "HOS (LAXMI)",
                "vendor_key": "HOS",
                "upc": "456",
                "source": "Inventory (POS)",
                "unit_cost": 15.0,
                "on_hand": 5,
            },
        ]
    )
    hits = search_products("lays", index)
    assert len(hits) == 1
    assert hits.iloc[0]["vendor_name"] == "PREMIER FOODS"

    vendors = vendors_for_query("lays", index)
    assert len(vendors) == 1
    assert vendors.iloc[0]["matching_products"] == 1
