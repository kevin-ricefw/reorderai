"""Build Excel order sheet from a saved detect-order run."""

from __future__ import annotations

import io
from typing import Any, Callable

import pandas as pd


def _cheaper_flag(it: dict[str, Any]) -> str:
    cheapest = it.get("cheapest_vendor")
    if it.get("cheaper_elsewhere") and cheapest:
        price = cheapest.get("price")
        price_s = f"${float(price):.2f}" if price is not None else ""
        return f"Yes — {cheapest.get('vendor_name', '')} {price_s}".strip()
    return ""


def _uncertainty(it: dict[str, Any]) -> float | None:
    p90, p50 = it.get("p90_demand"), it.get("p50_demand")
    return (p90 - p50) if p90 is not None and p50 is not None else None


def _uplifted(it: dict[str, Any]) -> float | None:
    p90, mult = it.get("p90_demand"), it.get("uplift_multiplier")
    return (p90 * mult) if p90 is not None and mult is not None else None


def _stock_capacity(it: dict[str, Any]) -> float | None:
    # 0 means "no cap" per backend — blank, not zero (mirrors index.html UI).
    v = it.get("wecomm_max_on_hand")
    return v if v else None


def _export_cols(x_days: int | None) -> list[tuple[str, Callable[[dict[str, Any]], Any]]]:
    x_label = f"ADS × {int(x_days)}d" if x_days and int(x_days) > 0 else "ADS × X"
    return [
        ("Action", lambda it: it.get("line_action")),
        ("Urgency", lambda it: it.get("urgency")),
        ("Product", lambda it: it.get("description")),
        ("UPC", lambda it: it.get("upc")),
        ("SKU", lambda it: it.get("sku")),
        ("Price", lambda it: it.get("vendor_price")),
        ("On Hand", lambda it: it.get("available_stock")),
        ("Days of Supply", lambda it: it.get("days_of_supply")),
        ("ADS / day", lambda it: it.get("ads")),
        (x_label, lambda it: it.get("ads_times_x")),
        ("Reorder Point (ROP)", lambda it: it.get("reorder_point")),
        ("Below ROP", lambda it: it.get("below_reorder_point")),
        ("Desired Stock", lambda it: it.get("desired_stock")),
        ("Stock at Arrival", lambda it: it.get("projected_stock_at_arrival")),
        ("AI Target (cover+SS+uplift)", lambda it: it.get("ai_target_qty")),
        ("Qty to Order", lambda it: it.get("qty_to_order")),
        ("Cases to Order", lambda it: it.get("cases_to_order")),
        ("Pack Size", lambda it: it.get("box_qty")),
        ("Lead Demand (L)", lambda it: it.get("lead_demand_ads")),
        ("Cover Demand (X−L)", lambda it: it.get("cover_demand_ads")),
        ("SS(L)", lambda it: it.get("safety_stock")),
        ("SS(X−L)", lambda it: it.get("safety_stock_cover")),
        ("ADS Cover (X−L)+SS", lambda it: it.get("ads_cover_qty")),
        ("P90 Sales Pred", lambda it: it.get("p90_demand")),
        ("Uncertainty (P90−P50)", _uncertainty),
        ("Uplifted (with feature)", _uplifted),
        ("Uplift ×", lambda it: it.get("uplift_multiplier")),
        ("Uplift Rule", lambda it: it.get("uplift_rule")),
        ("Flag (cheaper by other vendor)", _cheaper_flag),
        ("Stock Capacity", _stock_capacity),
        ("Last Invoice Qty", lambda it: it.get("last_pallet_qty")),
        ("Demand Class", lambda it: it.get("demand_class")),
    ]


def order_run_to_excel_bytes(run: dict[str, Any]) -> bytes:
    items = list(run.get("items") or [])
    x_days = run.get("x_days")
    try:
        x_days_i = int(x_days) if x_days is not None else None
    except (TypeError, ValueError):
        x_days_i = None
    cols = _export_cols(x_days_i)

    rows = [{label: extractor(it) for label, extractor in cols} for it in items]

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Order")
    return buf.getvalue()
