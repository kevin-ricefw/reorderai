"""Tests for waste report parsing."""

import pandas as pd

from v2.analytics.waste_report import parse_waste_upload, summarize_waste, waste_by_product


def test_parse_waste_upload_basic():
    raw = pd.DataFrame(
        {
            "Product": ["LAYS CLASSIC", "MILK 1G"],
            "Qty": [5, 2],
            "Total Cost": [12.5, 6.0],
        }
    )
    buf = __import__("io").BytesIO()
    raw.to_excel(buf, index=False)
    detail, col_map = parse_waste_upload(buf.getvalue(), filename="waste.xlsx")
    assert len(detail) == 2
    assert col_map["product"] == "Product"
    assert detail["total_cost"].sum() == 18.5


def test_summarize_waste():
    detail = pd.DataFrame(
        {
            "product_name": ["A", "A", "B"],
            "quantity_dumped": [2, 3, 1],
            "total_cost": [4.0, 6.0, 5.0],
            "unit_cost": [2, 2, 5],
            "reason": ["expired", "expired", "damaged"],
        }
    )
    s = summarize_waste(detail)
    assert s["unique_products"] == 2
    assert s["total_units_dumped"] == 6
    assert s["total_cost"] == 15.0
    by_prod = waste_by_product(detail)
    assert by_prod.iloc[0]["product_name"] == "A"
    assert by_prod.iloc[0]["quantity_dumped"] == 5
