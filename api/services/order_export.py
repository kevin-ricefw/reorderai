"""Build Excel order sheet from a saved detect-order run."""

from __future__ import annotations

import io
from typing import Any

import pandas as pd


def _export_cols(x_days: int | None) -> list[tuple[str, str]]:
    x_label = f"ADS × {int(x_days)}d" if x_days and int(x_days) > 0 else "ADS × X"
    return [
        ("line_action", "Action"),
        ("urgency", "Urgency"),
        ("description", "Product"),
        ("upc", "UPC"),
        ("sku", "SKU"),
        ("available_stock", "On Hand"),
        ("days_of_supply", "Days of Supply"),
        ("ads", "ADS / day"),
        ("ads_times_x", x_label),
        ("reorder_point", "Reorder Point (ROP)"),
        ("below_reorder_point", "Below ROP"),
        ("desired_stock", "Desired Stock"),
        ("projected_stock_at_arrival", "Stock at Arrival"),
        ("ai_target_qty", "AI Target (cover+SS+uplift)"),
        ("qty_to_order", "Qty to Order"),
        ("cases_to_order", "Cases to Order"),
        ("box_qty", "Pack Size"),
        ("lead_demand_ads", "Lead Demand (L)"),
        ("cover_demand_ads", "Cover Demand (X−L)"),
        ("safety_stock", "SS(L)"),
        ("safety_stock_cover", "SS(X−L)"),
        ("ads_cover_qty", "ADS Cover (X−L)+SS"),
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
