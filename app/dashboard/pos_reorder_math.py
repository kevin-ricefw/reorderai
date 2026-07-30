"""Exact POS reorder math — 30-day ADS formula as specified."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from v2.inventory_math.reorder_point import calculate_dynamic_reorder_point
from v2.inventory_math.safety_stock import calculate_safety_stock

SERVICE_LEVEL = 0.95
DEFAULT_ADS_WINDOW = 30


def _daily_totals_with_zeros(
    daily_sales: pd.DataFrame,
    *,
    ads_window_days: int,
    as_of_date: pd.Timestamp | None = None,
) -> tuple[pd.Series, float, pd.Timestamp, pd.Timestamp]:
    """
    Build a contiguous daily quantity series for the lookback window.

    Zero-sale days are included (quantity 0). Without this, intermittent SKUs
    (few burst days) get a massively inflated demand_std and over-order.

    Window end is ``as_of_date`` when provided; otherwise the SKU's last sale date.
    """
    df = daily_sales.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if df.empty:
        return pd.Series(dtype=float), 0.0, pd.NaT, pd.NaT

    ref = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else df["date"].max().normalize()
    start = ref - pd.Timedelta(days=ads_window_days - 1)
    window = df[(df["date"] >= start) & (df["date"] <= ref)].copy()
    window["date"] = window["date"].dt.normalize()
    daily_totals = window.groupby("date")["quantity"].sum() if not window.empty else pd.Series(dtype=float)
    full_idx = pd.date_range(start, ref, freq="D")
    filled = daily_totals.reindex(full_idx, fill_value=0.0).astype(float)
    total_sold = float(filled.sum())
    return filled, total_sold, start, ref


def compute_pos_ai_min(
    daily_sales: pd.DataFrame,
    lead_time_days: float,
    *,
    ads_window_days: int = DEFAULT_ADS_WINDOW,
    extra_sold_units: float = 0.0,
    as_of_date: pd.Timestamp | str | None = None,
) -> dict[str, Any]:
    """
    AI min on hand (ROP) per product using the exact store formula:

    Step 1: ADS = (POS sold in lookback + extra_sold_units) ÷ lookback days
            extra_sold_units = |negative on-hand| when count went negative
            (sold but receive was never added to inventory count)
    Step 2: Lead time = vendor delivery / order-cover days
    Step 3: Safety stock = 1.65 × demand_std × √(lead time)
            demand_std uses every calendar day in the lookback (zeros included)
            and is capped so SS cannot exceed lead-time demand
    Step 4: AI min = (ADS × lead time) + safety stock
    """
    lead = max(float(lead_time_days), 1.0)
    extra = max(float(extra_sold_units or 0.0), 0.0)
    empty = {
        "ads": 0.0,
        "demand_std": 0.0,
        "lead_time_days": int(round(lead)),
        "lead_time_demand": 0,
        "safety_stock": 0,
        "ai_min": 0,
        "total_sold_30d": 0,
        "extra_sold_from_negative_stock": 0,
        "ads_window_days": ads_window_days,
        "formula": "(ADS × lead) + safety_stock",
    }
    has_sales = (
        daily_sales is not None
        and not daily_sales.empty
        and "date" in getattr(daily_sales, "columns", [])
    )
    if not has_sales and extra <= 0:
        return empty

    total_sold = 0.0
    demand_std = 0.0
    if has_sales:
        as_of = pd.Timestamp(as_of_date) if as_of_date is not None else None
        filled, total_sold, _, _ = _daily_totals_with_zeros(
            daily_sales,
            ads_window_days=ads_window_days,
            as_of_date=as_of,
        )
        if len(filled) > 1:
            demand_std = float(filled.std(ddof=1))
            if np.isnan(demand_std):
                demand_std = 0.0

    total_sold += extra
    ads = total_sold / ads_window_days
    lead_time_demand = ads * lead

    safety = calculate_safety_stock(ads, demand_std, lead, SERVICE_LEVEL)
    # Intermittent / bursty SKUs: do not let safety exceed expected cover demand.
    # Otherwise 15-day cover orders many weeks of stock (e.g. Ashoka samosa).
    if lead_time_demand > 0:
        safety = min(float(safety), float(lead_time_demand))
    else:
        safety = 0.0

    rop = calculate_dynamic_reorder_point(ads, lead, safety)
    ai_min = max(int(round(rop)), 0)

    formula = (
        f"({round(ads, 1)} × {int(round(lead))}) + {int(round(safety))} = {ai_min}"
    )
    if extra > 0:
        formula = f"ADS includes +{int(round(extra))} from neg. count; {formula}"

    return {
        "ads": round(ads, 2),
        "demand_std": round(demand_std, 2),
        "lead_time_days": int(round(lead)),
        "lead_time_demand": int(round(lead_time_demand)),
        "safety_stock": int(round(safety)),
        "ai_min": ai_min,
        "total_sold_30d": int(round(total_sold)),
        "extra_sold_from_negative_stock": int(round(extra)),
        "ads_window_days": ads_window_days,
        "formula": formula,
    }
