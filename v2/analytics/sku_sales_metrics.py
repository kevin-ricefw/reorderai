"""SKU-level sales metrics, rankings, and top-100 scoring."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

ANALYSIS_START = date(2026, 1, 7)
ANALYSIS_END = date(2026, 7, 23)


def _rank_pct(series: pd.Series) -> pd.Series:
    """Percentile rank 0-1 where 1.0 = highest value in series."""
    return series.rank(method="average", ascending=True, pct=True)


def compute_sku_sales_metrics(
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
    *,
    start_date: date = ANALYSIS_START,
    end_date: date = ANALYSIS_END,
    ads_window_days: int = 30,
) -> pd.DataFrame:
    """
    Compute sales metrics for every SKU in the analysis period.
    """
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    calendar_days = (end_ts - start_ts).days + 1
    num_weeks = max(calendar_days / 7, 1)

    sales = sales.copy()
    sales["date"] = pd.to_datetime(sales["date"], errors="coerce")
    sales = sales[(sales["date"] >= start_ts) & (sales["date"] <= end_ts)]
    sales["upc"] = sales["upc"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

    inv = inventory.copy()
    inv["upc"] = inv["upc"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)

    # SKU master from inventory + any SKU that sold
    sold_skus = sales.groupby("upc", as_index=False).agg(
        product_name=("description", "first"),
        total_quantity=("quantity", "sum"),
        total_revenue=("revenue", "sum"),
        transaction_count=("transactions", "sum"),
        days_with_sales=("date", "nunique"),
        promotion_days=("on_promotion", "sum"),
    )

    inv_cols = inv[
        ["upc", "description", "QuantityOnHand", "vendor_name", "pack", "cost", "normal_price"]
    ].rename(columns={"description": "inv_description", "QuantityOnHand": "current_inventory"})
    inv_cols["upc"] = inv_cols["upc"].astype(str).str.strip()
    inv_cols["vendor_name"] = (
        inv_cols["vendor_name"].fillna("Unknown").astype(str).str.strip().replace({"": "Unknown", "nan": "Unknown"})
    )

    metrics = sold_skus.merge(inv_cols, on="upc", how="left")
    metrics = metrics[metrics["upc"].notna() & (metrics["upc"].astype(str).str.strip() != "")]
    metrics["product_name"] = metrics["product_name"].fillna(metrics["inv_description"]).fillna("")
    metrics["vendor_name"] = (
        metrics["vendor_name"].fillna("Unknown").astype(str).str.strip().replace({"": "Unknown", "nan": "Unknown"})
    )
    metrics["current_inventory"] = pd.to_numeric(metrics["current_inventory"], errors="coerce")
    metrics["pack_size"] = pd.to_numeric(metrics["pack"], errors="coerce").fillna(1).astype(int)

    metrics["days_with_zero_sales"] = calendar_days - metrics["days_with_sales"]
    metrics["sales_frequency"] = metrics["days_with_sales"] / calendar_days
    metrics["ads"] = metrics["total_quantity"] / calendar_days
    metrics["avg_weekly_sales"] = metrics["total_quantity"] / num_weeks

    # Recent 30-day ADS for reorder (use last 30 days of data)
    recent_start = end_ts - pd.Timedelta(days=ads_window_days - 1)
    recent = sales[sales["date"] >= recent_start]
    recent_agg = recent.groupby("upc")["quantity"].sum().reset_index(name="qty_last_30d")
    metrics = metrics.merge(recent_agg, on="upc", how="left")
    metrics["qty_last_30d"] = metrics["qty_last_30d"].fillna(0)
    metrics["ads_30d"] = metrics["qty_last_30d"] / ads_window_days

    metrics["analysis_start"] = start_date.isoformat()
    metrics["analysis_end"] = end_date.isoformat()
    metrics["calendar_days"] = calendar_days

    return metrics.sort_values("total_revenue", ascending=False).reset_index(drop=True)


def rank_skus(metrics: pd.DataFrame) -> pd.DataFrame:
    """Add revenue, quantity, frequency ranks and weighted top-100 score."""
    df = metrics.copy()

    df["revenue_rank"] = df["total_revenue"].rank(method="min", ascending=False).astype(int)
    df["quantity_rank"] = df["total_quantity"].rank(method="min", ascending=False).astype(int)
    df["frequency_rank"] = df["sales_frequency"].rank(method="min", ascending=False).astype(int)

    df["revenue_score"] = _rank_pct(df["total_revenue"])
    df["quantity_score"] = _rank_pct(df["total_quantity"])
    df["frequency_score"] = _rank_pct(df["sales_frequency"])

    df["weighted_score"] = (
        0.50 * df["revenue_score"]
        + 0.30 * df["quantity_score"]
        + 0.20 * df["frequency_score"]
    )
    df["overall_rank"] = df["weighted_score"].rank(method="min", ascending=False).astype(int)
    df["is_top100"] = df["overall_rank"] <= 100

    return df.sort_values("overall_rank").reset_index(drop=True)


def build_ranking_table(metrics: pd.DataFrame) -> pd.DataFrame:
    """Final output table with ranks and top-100 flag."""
    ranked = rank_skus(metrics)
    return ranked[
        [
            "upc",
            "product_name",
            "total_quantity",
            "total_revenue",
            "transaction_count",
            "ads",
            "ads_30d",
            "avg_weekly_sales",
            "sales_frequency",
            "days_with_sales",
            "days_with_zero_sales",
            "current_inventory",
            "revenue_rank",
            "quantity_rank",
            "frequency_rank",
            "weighted_score",
            "overall_rank",
            "is_top100",
        ]
    ].rename(
        columns={
            "upc": "SKU",
            "product_name": "Product Name",
            "is_top100": "IsTop100",
        }
    ).assign(IsTop100=lambda d: np.where(d["IsTop100"], "Yes", "No"))
