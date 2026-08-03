"""Build Excel order sheet from a saved detect-order run."""

from __future__ import annotations

import io
from typing import Any

import pandas as pd


def _export_cols(x_days: int | None) -> list[tuple[str, str]]:
    del x_days  # kept for call-site compat
    return [
        ("line_action", "Action"),
        ("urgency", "Urgency"),
        ("description", "Product"),
        ("upc", "UPC"),
        ("sku", "SKU"),
        ("available_stock", "On Hand"),
        ("days_of_supply", "Days of Supply"),
        ("ads", "ADS (units/day)"),
        ("reorder_point", "Reorder Point (ROP)"),
        ("below_reorder_point", "Below ROP"),
        ("min_on_hand", "Min On Hand"),
        ("min_on_hand_source", "Min Source"),
        ("wecomm_max_on_hand", "Max On Hand"),
        ("below_min_on_hand", "Below Min"),
        ("desired_stock", "Desired Stock"),
        ("projected_stock_at_arrival", "Stock at Arrival"),
        ("ai_target_qty", "AI Cover Target"),
        ("qty_to_order", "Qty to Order"),
        ("cases_to_order", "Cases to Order"),
        ("box_qty", "Pack Size"),
        ("lead_demand_ads", "Lead Demand (L)"),
        ("cover_demand_ads", "Cover Demand (C)"),
        ("safety_stock", "SS(L)"),
        ("safety_stock_cover", "SS(C)"),
        ("ads_cover_qty", "ADS Cover Qty"),
        ("uplift_multiplier", "Uplift ×"),
        ("uplift_rule", "Uplift Rule"),
        ("last_pallet_qty", "Last Invoice Qty"),
        ("demand_class", "Demand Class"),
    ]


def order_run_to_excel_bytes(run: dict[str, Any]) -> bytes:
    items = list(run.get("items") or [])
    x_days = run.get("x_days")
    try:
        x_days_i = int(x_days) if x_days is not None else None
    except (TypeError, ValueError):
        x_days_i = None
    cols = _export_cols(x_days_i)

    rows = []
    for it in items:
        rows.append({label: it.get(key) for key, label in cols})

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Order")
    return buf.getvalue()
