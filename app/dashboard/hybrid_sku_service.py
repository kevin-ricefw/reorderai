"""
On-demand Prophet + LightGBM residual walkthrough for one selected SKU.

Used by Vendor Reorder UI when the user picks a product and clicks Run.
Order-log features come from past invoice *receive cadence* (sheet dates + qty).
Expiry is never used.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from app.dashboard.past_invoice_patterns import load_past_invoice_lines
from app.dashboard.pos_data_service import build_enriched_sales_cached
from app.dashboard.vendor_reorder_service import _build_sales_index, _daily_sales_for_upc
from v2.inventory_math.hybrid_replenishment import run_hybrid_replenishment_engine

_DATE_PATTERNS = [
    re.compile(
        r"(\d{1,2})\s*(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s*(\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})"),
    re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})"),
]

_MONTH = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _parse_date_from_label(label: str) -> pd.Timestamp | None:
    s = str(label or "").strip()
    if not s:
        return None
    m = _DATE_PATTERNS[0].search(s)
    if m:
        day, mon, year = int(m.group(1)), _MONTH[m.group(2).upper()[:3]], int(m.group(3))
        if year < 100:
            year += 2000
        try:
            return pd.Timestamp(year=year, month=mon, day=day).normalize()
        except ValueError:
            return None
    m = _DATE_PATTERNS[1].search(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        # Prefer day-first (common on these sheets): D/M/Y, else M/D/Y
        for month, day in ((b, a), (a, b)):
            try:
                return pd.Timestamp(year=y, month=month, day=day).normalize()
            except ValueError:
                continue
        return None
    m = _DATE_PATTERNS[2].search(s)
    if m:
        try:
            return pd.Timestamp(
                year=int(m.group(1)), month=int(m.group(2)), day=int(m.group(3))
            ).normalize()
        except ValueError:
            return None
    return None


def build_order_log_for_sku(
    *,
    upc: str = "",
    vendor_name: str = "",
    description: str = "",
) -> pd.DataFrame:
    """
    Manual order-pattern log for LightGBM residuals.

    Prefer lines matching this UPC / product; fall back to same vendor receipts;
    last resort = all store receipt dates (cadence only).
    """
    lines = load_past_invoice_lines()
    if lines.empty:
        return pd.DataFrame(columns=["Received Date", "quantity"])

    df = lines.copy()
    upc_s = str(upc or "").strip()
    vendor_s = str(vendor_name or "").strip().upper()
    desc_u = str(description or "").upper().strip()

    matched = pd.DataFrame()
    if upc_s and "upc" in df.columns:
        bare = upc_s.lstrip("0") or upc_s
        matched = df[
            df["upc"].astype(str).str.strip().isin({upc_s, bare})
            | df["upc"].astype(str).str.lstrip("0").eq(bare)
        ]
    if matched.empty and desc_u and "norm_desc" in df.columns:
        key = re.sub(r"[^A-Z0-9]+", " ", desc_u)
        key = re.sub(r"\s+", " ", key).strip()
        if key:
            matched = df[df["norm_desc"].astype(str).str.contains(re.escape(key[:24]), na=False)]
    if matched.empty and vendor_s and "vendor_name" in df.columns:
        matched = df[df["vendor_name"].astype(str).str.upper().str.contains(vendor_s.split()[0], na=False)]
    if matched.empty:
        matched = df

    rows = []
    for _, r in matched.iterrows():
        dt = _parse_date_from_label(str(r.get("invoice_sheet") or ""))
        if dt is None:
            continue
        qty = float(r.get("units_ordered") or r.get("cases_ordered") or 1.0)
        rows.append({"Received Date": dt, "quantity": max(qty, 1.0)})

    if not rows:
        return pd.DataFrame(columns=["Received Date", "quantity"])

    out = pd.DataFrame(rows)
    # One row per receive day (sum qty if many lines same day)
    out = (
        out.groupby("Received Date", as_index=False)["quantity"]
        .sum()
        .sort_values("Received Date")
    )
    return out


def sales_frame_for_upc(upc: str) -> pd.DataFrame:
    """Daily sales for one UPC as date/sales for Prophet."""
    enriched = build_enriched_sales_cached()
    if enriched.empty:
        return pd.DataFrame(columns=["date", "sales"])
    sales_index = _build_sales_index(enriched)
    daily = _daily_sales_for_upc(enriched, str(upc).strip(), sales_index)
    if daily.empty:
        return pd.DataFrame(columns=["date", "sales"])
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.normalize()
    daily = daily.dropna(subset=["date"])
    daily = (
        daily.groupby("date", as_index=False)["quantity"]
        .sum()
        .rename(columns={"quantity": "sales"})
        .sort_values("date")
    )
    return daily


def run_hybrid_for_selected_product(
    *,
    upc: str,
    product_name: str,
    vendor_name: str,
    physical_stock: float,
    lead_time_days: int,
    days_to_cover: int,
) -> dict[str, Any]:
    """
    Full hybrid walkthrough payload for the Streamlit product panel.

    Returns metrics, decision, and a day-level table:
      Prophet yhat | residual̂ correction | hybrid | order-pattern features
    """
    sales = sales_frame_for_upc(upc)
    if len(sales) < 14:
        return {
            "ok": False,
            "error": (
                f"Need at least 14 days of sales for Prophet. "
                f"Found {len(sales)} day(s) for this UPC."
            ),
            "sales_days": len(sales),
        }

    order_log = build_order_log_for_sku(
        upc=upc, vendor_name=vendor_name, description=product_name
    )
    if order_log.empty:
        # Synthetic mild cadence so LightGBM still has features (zeros / sentinel)
        last = pd.Timestamp(sales["date"].max()).normalize()
        order_log = pd.DataFrame(
            {
                "Received Date": [last - pd.Timedelta(days=21), last - pd.Timedelta(days=14), last - pd.Timedelta(days=7)],
                "quantity": [1.0, 1.0, 1.0],
            }
        )
        order_log_note = "No matching past-invoice dates for this SKU — using neutral cadence placeholders."
    else:
        order_log_note = f"Order pattern from {len(order_log)} past receive day(s) (invoice sheets)."

    hybrid, decision = run_hybrid_replenishment_engine(
        sales,
        order_log,
        lead_time_days=lead_time_days,
        days_to_cover=days_to_cover,
        physical_stock=physical_stock,
    )

    future = hybrid.future.copy()
    future["prophet_yhat"] = future["yhat"].round(2)
    future["lgbm_correction"] = future["residual_hat"].round(2)
    future["hybrid_demand"] = future["hybrid_yhat"].round(2)
    show_cols = [
        "ds",
        "prophet_yhat",
        "lgbm_correction",
        "hybrid_demand",
        "yhat_upper",
        "days_since_last_receipt",
        "last_receipt_gap_days",
        "receipts_last_7d",
    ]
    future_view = future[[c for c in show_cols if c in future.columns]].copy()
    future_view["ds"] = pd.to_datetime(future_view["ds"]).dt.strftime("%Y-%m-%d")

    # Recent history: actual vs prophet vs hybrid (last 14 days)
    hist = hybrid.history.tail(14).copy()
    hist_view = pd.DataFrame(
        {
            "date": pd.to_datetime(hist["ds"]).dt.strftime("%Y-%m-%d"),
            "actual": hist["y"].round(2),
            "prophet_yhat": hist["yhat"].round(2),
            "residual_actual": hist["residual"].round(2),
            "lgbm_correction": hist["residual_hat"].round(2),
            "hybrid": hist["hybrid_yhat"].round(2),
        }
    )

    return {
        "ok": True,
        "product_name": product_name,
        "upc": upc,
        "sales_days": int(len(sales)),
        "sales_start": str(sales["date"].min().date()),
        "sales_end": str(sales["date"].max().date()),
        "order_log_note": order_log_note,
        "order_log_rows": int(len(order_log)),
        "decision": decision.to_dict(),
        "metrics": dict(hybrid.metrics),
        "future": future_view,
        "history_tail": hist_view,
        "story": {
            "prophet_said": decision.prophet_yhat_sum_window,
            "correction_total": round(
                decision.hybrid_demand_L_plus_C - decision.prophet_yhat_sum_window, 2
            ),
            "hybrid_total": decision.hybrid_demand_L_plus_C,
            "safety_stock": decision.safety_stock,
            "rop": decision.reorder_point,
            "stock": decision.physical_stock,
            "order_qty": decision.recommended_order_qty,
            "L": decision.lead_time_days,
            "C": decision.days_to_cover,
        },
    }
