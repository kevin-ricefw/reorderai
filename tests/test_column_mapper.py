"""Tests for column mapping helpers."""

import pandas as pd

from database.etl.column_mapper import INVENTORY_ALIASES, apply_mapping, match_columns


def test_inventory_column_mapping():
    src = pd.DataFrame(
        {
            "upc": ["1"],
            "description": ["Milk"],
            "cost": [2.5],
            "QuantityOnHand": [10],
            "vendor_name": ["HOS"],
        }
    )
    targets = ["UPC", "Description", "Cost", "QuantityOnHand", "VendorName"]
    mapping = match_columns(src, targets, INVENTORY_ALIASES)
    out = apply_mapping(src, mapping, targets)
    assert out.loc[0, "UPC"] == "1"
    assert out.loc[0, "Description"] == "Milk"
