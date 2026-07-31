"""Build Excel order sheet from a saved detect-order run."""

from __future__ import annotations

import io
from typing import Any

import pandas as pd


_EXPORT_COLS = [
    ("description", "Product"),
    ("upc", "UPC"),
    ("sku", "SKU"),
    ("available_stock", "On Hand"),
    ("ads", "ADS (units/day)"),
    ("lead_demand_ads", "Lead demand ADS (L days)"),
    ("cover_demand_ads", "Cover demand ADS (C days)"),
    ("safety_stock", "Safety Stock"),
    ("reorder_point", "AI Min / ROP"),
    ("below_reorder_point", "Below ROP"),
    ("ads_cover_qty", "ADS+SS for X=L+C"),
    ("uplift_multiplier", "Uplift ×"),
    ("uplift_rule", "Uplift Rule"),
    ("p50_demand", "P50 (X=L+C)"),
    ("p90_demand", "P90 (X=L+C)"),
    ("ai_target_qty", "AI Target"),
    ("qty_to_order", "Order Units"),
    ("cases_to_order", "Order Cases"),
    ("box_qty", "Pack / Case Size"),
    ("last_pallet_qty", "Last Invoice Qty"),
    ("demand_class", "Demand Class"),
    ("justification", "Why"),
]


def order_run_to_excel_bytes(run: dict[str, Any]) -> bytes:
    items = list(run.get("items") or [])
    rows = []
    for it in items:
        rows.append({label: it.get(key) for key, label in _EXPORT_COLS})

    summary = pd.DataFrame(
        [
            {
                "Vendor": (run.get("vendor") or {}).get("vendor_name"),
                "Vendor ID": (run.get("vendor") or {}).get("vendor_id"),
                "Run ID": run.get("run_id"),
                "Lead days (L)": run.get("lead_time_days"),
                "Cover days (C)": run.get("time_to_cover_days"),
                "Window X": run.get("x_days"),
                "Catalog SKUs": run.get("catalog_item_count") or run.get("item_count"),
                "Lines to order": run.get("order_line_count"),
                "Total units": run.get("total_units_to_order"),
                "Total cases": run.get("total_cases_to_order"),
                "Message": run.get("message"),
            }
        ]
    )
    detail = pd.DataFrame(rows)
    if detail.empty:
        detail = pd.DataFrame(columns=[label for _, label in _EXPORT_COLS])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary", index=False)
        detail.to_excel(writer, sheet_name="Order Sheet", index=False)
    return buf.getvalue()
