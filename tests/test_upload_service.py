"""Tests for sales/inventory upload validation helpers."""

from __future__ import annotations

from datetime import date

import pytest

from api.services.upload_service import (
    parse_sale_date_from_filename,
    sales_filename_for_date,
    validate_inventory_csv,
    validate_sales_csv,
)


def test_parse_sale_date_from_filename():
    assert parse_sale_date_from_filename("Product Sales JULY 23.csv", default_year=2026) == date(
        2026, 7, 23
    )
    assert sales_filename_for_date(date(2026, 7, 23)) == "Product Sales JULY 23.csv"


def test_validate_sales_csv_ok():
    csv = b"UPC,Description,Qty Sold,Net Sales\n123,Item,2,4.00\n"
    meta = validate_sales_csv(csv)
    assert meta["rows"] == 1
    assert meta["upc_column"] == "UPC"


def test_validate_sales_csv_missing_cols():
    with pytest.raises(ValueError, match="UPC"):
        validate_sales_csv(b"foo,bar\n1,2\n")


def test_validate_inventory_csv_ok():
    csv = b"upc,description,QuantityOnHand\n1,A,3\n2,B,0\n"
    meta = validate_inventory_csv(csv)
    assert meta["unique_upcs"] == 2
