"""Reorder recommendations combining formula (ROP) and ML demand forecasts."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.dashboard.pos_reorder_math import compute_pos_ai_min
from app.dashboard.vendor_catalog_loader import (
    DEFAULT_NO_SCHEDULE_COVER_DAYS,
    TRACKED_VENDORS,
    load_delivery_schedule,
    resolve_planning_cover_days,
)
from v2.inventory_math.pack_size import round_up_to_pack


def _vendor_lead_map(vendor_names: pd.Series | None = None) -> dict[str, int]:
    schedule = load_delivery_schedule()
    mapping: dict[str, int] = {}
    names = vendor_names.dropna().unique() if vendor_names is not None else []
    if len(names) == 0:
        for v in TRACKED_VENDORS:
            for name in v["inventory_names"]:
                lead, _ = resolve_planning_cover_days(name, schedule)
                mapping[name] = lead
        return mapping
    for name in names:
        lead, _ = resolve_planning_cover_days(str(name), schedule)
        mapping[str(name)] = lead
    return mapping


def compute_reorder_for_skus(
    metrics: pd.DataFrame,
    daily_sales: pd.DataFrame,
    forecasts: pd.DataFrame,
    *,
    ads_window_days: int = 30,
    confidence_from_r2: float = 0.5,
) -> pd.DataFrame:
    """
    Per-SKU reorder using:
      AI min = (ADS x lead) + safety stock
      ML-adjusted demand from 7/14/30d forecasts
    """
    vendor_lead = _vendor_lead_map(metrics.get("vendor_name"))
    daily = daily_sales.copy()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["upc"] = daily["upc"].astype(str).str.strip()
    as_of = daily["date"].max() if not daily.empty else None

    rows = []
    for _, m in metrics.iterrows():
        upc = str(m["upc"]).strip()
        sku_daily = daily[daily["upc"] == upc][["date", "quantity"]]
        vendor = str(m.get("vendor_name", "") or "")
        lead = vendor_lead.get(vendor, DEFAULT_NO_SCHEDULE_COVER_DAYS)
        pack = int(m.get("pack_size", 1) or 1)

        calc = compute_pos_ai_min(
            sku_daily,
            lead,
            ads_window_days=ads_window_days,
            extra_sold_units=max(0.0, -float(m.get("current_inventory") or 0)),
            as_of_date=as_of,
        )
        ai_min = int(calc["ai_min"])
        # −45 → 45 sold into ADS; on-hand for need treated as 0
        stock_raw = float(m.get("current_inventory") or 0)
        stock = max(stock_raw, 0.0)

        fc = forecasts[forecasts["upc"] == upc] if not forecasts.empty else pd.DataFrame()
        fc7 = float(fc["forecast_7d"].iloc[0]) if not fc.empty and "forecast_7d" in fc.columns else 0
        fc14 = float(fc["forecast_14d"].iloc[0]) if not fc.empty and "forecast_14d" in fc.columns else 0
        fc30 = float(fc["forecast_30d"].iloc[0]) if not fc.empty and "forecast_30d" in fc.columns else 0

        raw_need = max(0, ai_min - stock)
        ml_need_14 = max(0, fc14 - stock) if fc14 > 0 else raw_need
        recommended_raw = max(raw_need, ml_need_14)
        order_qty = round_up_to_pack(recommended_raw, pack)

        ads = float(calc["ads"])
        days_until_stockout = (stock / ads) if ads > 0 and stock > 0 else (0.0 if stock <= 0 else 999.0)
        order_now = stock <= ai_min or (fc7 > stock and fc7 > 0)

        rows.append(
            {
                "upc": upc,
                "product_name": m.get("product_name", ""),
                "vendor_name": vendor,
                "current_inventory": round(stock_raw, 2),
                "ads": calc["ads"],
                "ads_30d": m.get("ads_30d", calc["ads"]),
                "lead_time_days": lead,
                "lead_time_demand": calc["lead_time_demand"],
                "safety_stock": calc["safety_stock"],
                "reorder_point": ai_min,
                "formula_raw_need": int(round(raw_need)),
                "forecast_7d": round(fc7, 2),
                "forecast_14d": round(fc14, 2),
                "forecast_30d": round(fc30, 2),
                "recommended_raw_qty": int(round(recommended_raw)),
                "pack_size": pack,
                "recommended_order_qty": order_qty,
                "order_now": "Yes" if order_now and order_qty > 0 else "No",
                "days_until_stockout": round(days_until_stockout, 1),
                "confidence_score": round(min(max(confidence_from_r2, 0.3), 0.95), 3),
                "formula_breakdown": calc["formula"],
            }
        )

    return pd.DataFrame(rows)


def merge_rankings_and_reorder(ranking_table: pd.DataFrame, reorder: pd.DataFrame) -> pd.DataFrame:
    """Single master output table."""
    r = ranking_table.rename(columns={"SKU": "upc"})
    r["upc"] = r["upc"].astype(str).str.strip()
    reorder["upc"] = reorder["upc"].astype(str).str.strip()
    merged = r.merge(reorder, on="upc", how="left", suffixes=("", "_reorder"))
    if "product_name_reorder" in merged.columns:
        merged["Product Name"] = merged["Product Name"].fillna(merged["product_name_reorder"])
    return merged
