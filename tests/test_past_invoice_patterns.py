"""Past invoice pattern learning tests."""

import pandas as pd

from app.dashboard.past_invoice_patterns import (
    _extract_ours_qty_from_text,
    _load_one_sheet_from_frame,
    _resolve_ours_cases,
    apply_invoice_order_cap,
)


def test_invoice_cap_reduces_oversized_order():
    pattern = {
        "median_units": 20,
        "p90_units": 40,
        "max_units": 48,
        "order_count": 6,
    }
    qty, note = apply_invoice_order_cap(200, pattern, buffer=1.25)
    assert qty == 60  # max(p90=40, max=48) * 1.25
    assert "Capped" in note


def test_invoice_cap_does_not_raise_small_order():
    pattern = {
        "median_units": 20,
        "p90_units": 40,
        "max_units": 48,
        "order_count": 6,
    }
    qty, note = apply_invoice_order_cap(30, pattern, buffer=1.25)
    assert qty == 30
    assert note == ""


def test_infer_pack_from_amul_desc():
    from app.dashboard.past_invoice_patterns import _infer_pack_from_description

    assert _infer_pack_from_description("AMUL MILK - GOLD 6% FAT 4/1 GAL") == 4.0


def test_invoice_cap_uses_cases_times_pack():
    # Hist max 12 cases, pack 4 → cap units around 12*4*1.25 = 60
    pattern = {
        "median_cases": 6,
        "p90_cases": 11,
        "max_cases": 12,
        "median_units": 12,  # wrong/old units-as-cases pollution
        "p90_units": 12,
        "max_units": 12,
        "order_count": 8,
    }
    qty, note = apply_invoice_order_cap(80, pattern, buffer=1.25, pack_size=4)
    assert qty == 60
    assert "Capped" in note


def test_parse_ours_note():
    assert _extract_ours_qty_from_text("ours 8") == 8.0
    assert _extract_ours_qty_from_text("ours:6") == 6.0
    assert _extract_ours_qty_from_text("8 ours") == 8.0
    assert _extract_ours_qty_from_text("swadesh 4") == 4.0


def test_shared_second_qty_is_ours():
    # Carloes K-S style: total 8, ours 6
    row = pd.Series({"qty_ordered": 8, "qty_ordered_1": 6})
    ours, total, src, kind = _resolve_ours_cases(row, "CARLOES K-S ON 01 JULY")
    assert ours == 6
    assert total == 8
    assert src == "shared_second_qty"
    assert kind == "cases"


def test_swadesh_column_preferred():
    row = pd.Series({"qty_ours": 0.5, "qty_ordered": 2})
    ours, total, src, kind = _resolve_ours_cases(row, "KB FORM 24 FEB 26")
    assert ours == 0.5
    assert src == "swadesh_col"


def test_kisan_tagged_row_skipped():
    raw = pd.DataFrame(
        [
            [
                "S.NO",
                "VENDOR NAME",
                "DESCRIPTION",
                "CASE COST",
                "CASE COUNT",
                "CURRENT COST",
                "PRICE",
                "QTY GIVEN NUM",
                "REMARKS",
            ],
            [1, "OM PRODUCE", "GOPI PANEER 15/14 OZ", 82.5, 15, 5.5, 7.99, 1.0, "kisan"],
            [2, "OM PRODUCE", "TOMATO ROUND 25 LB", 15.0, 25, 0.6, 0.99, 2.0, ""],
        ]
    )
    df = _load_one_sheet_from_frame(raw, "OM PRODUCE 21 JAN 2026")
    assert len(df) == 1
    assert df.iloc[0]["description"] == "TOMATO ROUND 25 LB"


def test_carloes_ks_sheet_uses_ours_not_pallet():
    raw = pd.DataFrame(
        [
            [
                "S.NO",
                "VENDOR NAME",
                "DESCRIPTION",
                "CASE COST",
                "QTY GIVEN NUM",
                "CASE COUNT",
                "CURRENT COST",
                "PRICE",
                "QTY GIVEN NUM",
                "QTY ON HAND",
            ],
            [1, "CARLOES", "OKRA INDIAN", 28.5, 8, 30, 0.95, 1.99, 6, 40],
            [2, "CARLOES", "CILANTRO-60CT", 30.5, 3, 60, 0.5, 0.99, 2, 120],
            [3, "CARLOES", "12CT CAULIFLOWER", 29, 1, None, None, None, "8PC", None],
        ]
    )
    df = _load_one_sheet_from_frame(raw, "CARLOES K-S ON 01 JULY")
    assert len(df) == 3
    okra = df[df["description"] == "OKRA INDIAN"].iloc[0]
    assert okra["cases_ordered"] == 6
    assert okra["cases_total_shared"] == 8
    assert bool(okra["shared_order"]) is True
    cauli = df[df["description"] == "12CT CAULIFLOWER"].iloc[0]
    assert cauli["units_ordered"] == 8
    assert cauli["qty_source"] == "shared_pieces"
