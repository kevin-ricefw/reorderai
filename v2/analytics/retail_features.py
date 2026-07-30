"""Retail forecasting features — lags, rolling sales, inventory, promotion, calendar."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from v2.forecasting.calendar_enrichment import merge_calendar_features

# All daily columns (full export).
CORRELATION_FEATURE_COLUMNS: dict[str, str] = {
    "total_units": "Daily units sold",
    "total_revenue": "Daily revenue ($)",
    "sku_count": "SKUs sold (count)",
    "is_weekend": "Weekend",
    "lag_1": "Yesterday sales",
    "lag_7": "Sales 7 days ago",
    "lag_14": "Sales 14 days ago",
    "lag_28": "Sales 28 days ago",
    "rolling_7": "Rolling 7-day avg sales",
    "rolling_30": "Rolling 30-day avg sales",
    "promo_rate": "Promotion rate (% SKUs on deal)",
}

# Strong predictors only — used in heatmap (weak calendar/weather/price removed).
HEATMAP_FEATURE_COLUMNS: dict[str, str] = {
    "total_units": "Daily units sold",
    "sku_count": "SKUs sold (count)",
    "is_weekend": "Weekend (Sat/Sun)",
    "lag_1": "Yesterday sales",
    "lag_7": "Sales 7 days ago",
    "lag_14": "Sales 14 days ago",
    "lag_28": "Sales 28 days ago",
    "rolling_7": "Rolling 7-day avg sales",
    "rolling_30": "Rolling 30-day avg sales",
}

# Candidate attributes kept for EDA after dropping noise / constant columns.
# Removed: weather (weak for orders), store-wide festival/payday/month-end/school,
# static inventory snapshot, store-level ADI/CV2 (constant across days).
CANDIDATE_HEATMAP_FEATURE_COLUMNS: dict[str, str] = {
    "total_units": "Daily units sold",
    "sku_count": "SKUs sold (count)",
    "is_weekend": "Weekend (Sat/Sun)",
    "lag_1": "Yesterday sales",
    "lag_7": "Sales 7 days ago",
    "lag_14": "Sales 14 days ago",
    "lag_28": "Sales 28 days ago",
    "rolling_7": "Rolling 7-day avg sales",
    "rolling_30": "Rolling 30-day avg sales",
    "promo_rate": "Promotion rate (% SKUs on deal)",
    "avg_discount_pct": "Avg discount %",
}

# Plain-English guide for team / stakeholders.
ATTRIBUTE_GUIDE_ROWS: list[dict[str, str]] = [
    {
        "attribute": "Daily units sold",
        "what_it_means": "Total items sold in the store that day (target we predict)",
        "example": "710 units on Jan 10",
        "use_for_training": "Yes — this is what the model learns to forecast",
        "strength": "Target",
    },
    {
        "attribute": "SKUs sold (count)",
        "what_it_means": "How many different products had at least one sale",
        "example": "348 SKUs sold on Jan 10",
        "use_for_training": "Yes — busy days sell more total units",
        "strength": "Strong",
    },
    {
        "attribute": "Weekend",
        "what_it_means": "1 if Saturday or Sunday, else 0",
        "example": "1 on Jan 10 (Saturday)",
        "use_for_training": "Yes — weekends sell more in our data",
        "strength": "Strong",
    },
    {
        "attribute": "Yesterday sales",
        "what_it_means": "Total units sold the previous day (lag 1)",
        "example": "474 units (day before 710)",
        "use_for_training": "Yes — recent demand predicts tomorrow",
        "strength": "Strong (SKU model)",
    },
    {
        "attribute": "Sales 7 / 14 / 28 days ago",
        "what_it_means": "Same weekday or cycle from 1, 2, or 4 weeks back",
        "example": "450 units exactly 7 days earlier",
        "use_for_training": "Yes — captures weekly patterns",
        "strength": "Strong",
    },
    {
        "attribute": "Rolling 7 / 30-day avg",
        "what_it_means": "Average daily sales over the past week or month",
        "example": "574 avg units/day over last 7 days",
        "use_for_training": "Yes — smooths noise, shows trend",
        "strength": "Strong",
    },
    {
        "attribute": "Promotion / discount %",
        "what_it_means": "Was the item on deal? How much off list price?",
        "example": "Deal flag or 10% off",
        "use_for_training": "Yes — used in SKU-level LightGBM model",
        "strength": "Strong (SKU model)",
    },
    {
        "attribute": "Inventory on hand",
        "what_it_means": "Units in stock now (from inventory file)",
        "example": "20 packs on shelf",
        "use_for_training": "Yes — can't sell what you don't have",
        "strength": "Strong (SKU model)",
    },
    {
        "attribute": "Out of stock flag",
        "what_it_means": "1 if stock is zero, else 0",
        "example": "0 = in stock, 1 = OOS",
        "use_for_training": "Yes — OOS days show zero sales",
        "strength": "Strong (SKU model)",
    },
    {
        "attribute": "Reorder point / safety stock",
        "what_it_means": "Formula: (avg daily sales × lead time) + buffer stock",
        "example": "ROP = 12 units, safety = 3",
        "use_for_training": "Yes — tells model normal replenishment level",
        "strength": "Moderate",
    },
    {
        "attribute": "Vendor lead time",
        "what_it_means": "Days from order cut-off to delivery for that vendor",
        "example": "7 days for scheduled vendor",
        "use_for_training": "Yes — longer lead = plan further ahead",
        "strength": "Moderate",
    },
    {
        "attribute": "Unit price ($)",
        "what_it_means": "Selling price per item from POS or inventory",
        "example": "$5.99",
        "use_for_training": "Yes — price affects demand",
        "strength": "Moderate (SKU model)",
    },
]


def _parse_money(value: str | float | None) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    s = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def add_retail_calendar_columns(daily: pd.DataFrame, *, date_col: str = "date") -> pd.DataFrame:
    """Weekend, festivals, school breaks, month-end/start, payday proxy."""
    out = daily.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
    out = merge_calendar_features(out, date_col)
    out["is_weekend"] = out["is_weekend"].astype(int)
    out["is_indian_festival"] = out["indian_festival"].notna().astype(int)
    out["is_festival"] = out["is_indian_festival"]

    d = out[date_col].dt
    out["is_month_end"] = (d.day >= d.days_in_month - 2).astype(int)
    out["is_month_start"] = (d.day <= 3).astype(int)
    out["is_payday_window"] = d.day.between(14, 16).astype(int)
    out["is_school_break"] = out[date_col].apply(_is_michigan_school_break).astype(int)

    drop = [c for c in ("us_holiday", "indian_festival", "day_type", "day_name", "day_of_week", "is_weekday", "is_long_weekend") if c in out.columns]
    return out.drop(columns=drop)


def _is_michigan_school_break(ts: pd.Timestamp) -> bool:
    """Approximate Okemos / East Lansing school breaks for 2026."""
    d = ts.date() if hasattr(ts, "date") else ts
    if not isinstance(d, date):
        return False
    y, m, day = d.year, d.month, d.day
    if y != 2026:
        # Generic US K-12 pattern for other years
        if m in (6, 7, 8):
            return True
        if m == 12 and day >= 20:
            return True
        if m == 1 and day <= 5:
            return True
        return m == 3 and 15 <= day <= 28

    if (m == 12 and day >= 22) or (m == 1 and day <= 3):
        return True  # winter break
    if m == 3 and 23 <= day <= 27:
        return True  # spring break
    if (m == 6 and day >= 12) or m == 7 or (m == 8 and day <= 24):
        return True  # summer
    return False


def add_sales_lag_features(daily: pd.DataFrame, *, units_col: str = "total_units") -> pd.DataFrame:
    """Yesterday + 7/14/28-day lags and rolling 7/30 averages on store daily units."""
    out = daily.sort_values("date").reset_index(drop=True).copy()
    s = out[units_col]
    out["lag_1"] = s.shift(1)
    out["lag_7"] = s.shift(7)
    out["lag_14"] = s.shift(14)
    out["lag_28"] = s.shift(28)
    out["rolling_7"] = s.shift(1).rolling(7, min_periods=1).mean()
    out["rolling_30"] = s.shift(1).rolling(30, min_periods=1).mean()
    return out


def aggregate_store_daily_from_sales(sales: pd.DataFrame) -> pd.DataFrame:
    """One row per day: units, revenue, SKU count, promotion & price signals."""
    if sales.empty:
        return pd.DataFrame()

    df = sales.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)

    if "on_promotion" in df.columns:
        df["on_promotion"] = df["on_promotion"].astype(int)
    else:
        df["on_promotion"] = 0

    if "list_price" in df.columns:
        df["list_price"] = pd.to_numeric(df["list_price"], errors="coerce").fillna(0)
    else:
        df["list_price"] = np.where(df["quantity"] > 0, df["revenue"] / df["quantity"], 0)

    if "discount_pct" not in df.columns:
        df["discount_pct"] = np.where(
            (df["list_price"] > 0) & (df["quantity"] > 0),
            np.clip((1 - (df["revenue"] / df["quantity"]) / df["list_price"]) * 100, 0, 100),
            0.0,
        )

    daily = (
        df.groupby("date", as_index=False)
        .agg(
            total_units=("quantity", "sum"),
            total_revenue=("revenue", "sum"),
            sku_count=("upc", "nunique"),
            promo_sku_count=("on_promotion", "sum"),
        )
    )

    promo_by_day = (
        df[df["on_promotion"] == 1]
        .groupby("date")["quantity"]
        .sum()
        .rename("promo_units")
    )
    daily = daily.merge(promo_by_day, on="date", how="left")
    daily["promo_units"] = daily["promo_units"].fillna(0)

    price_day = df.groupby("date").apply(
        lambda g: float(g["revenue"].sum() / g["quantity"].sum()) if g["quantity"].sum() > 0 else 0.0,
        include_groups=False,
    )
    daily["avg_unit_price"] = daily["date"].map(price_day).fillna(0)

    disc_day = df.groupby("date").apply(
        lambda g: float(g.loc[g["discount_pct"] > 0, "discount_pct"].mean()) if (g["discount_pct"] > 0).any() else 0.0,
        include_groups=False,
    )
    daily["avg_discount_pct"] = daily["date"].map(disc_day).fillna(0)

    daily["promo_rate"] = np.where(daily["sku_count"] > 0, daily["promo_sku_count"] / daily["sku_count"] * 100, 0)
    daily["promo_units_share"] = np.where(daily["total_units"] > 0, daily["promo_units"] / daily["total_units"] * 100, 0)
    daily = daily.drop(columns=["promo_sku_count", "promo_units"], errors="ignore")
    return daily


def attach_store_inventory_snapshot(
    daily: pd.DataFrame,
    inventory: pd.DataFrame,
    *,
    sales: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Attach static store inventory snapshot metrics (same value each day — for ML; weak in daily corr)."""
    if inventory.empty:
        for col in ("store_units_on_hand", "oos_sku_count", "oos_rate", "avg_vendor_lead_time", "avg_reorder_point", "avg_safety_stock"):
            daily[col] = 0.0
        return daily

    inv = inventory.copy()
    inv["upc"] = inv["upc"].astype(str).str.strip()
    inv["QuantityOnHand"] = pd.to_numeric(inv.get("QuantityOnHand"), errors="coerce").fillna(0)

    from app.dashboard.vendor_catalog_loader import (
        DEFAULT_NO_SCHEDULE_COVER_DAYS,
        load_delivery_schedule,
        resolve_planning_cover_days,
    )
    from app.dashboard.pos_reorder_math import compute_pos_ai_min

    schedule = load_delivery_schedule()
    vendor_lead: dict[str, int] = {}
    for name in inv["vendor_name"].dropna().astype(str).unique():
        lead, _ = resolve_planning_cover_days(name, schedule)
        vendor_lead[name] = lead
    inv["vendor_lead_time"] = inv["vendor_name"].astype(str).map(vendor_lead).fillna(DEFAULT_NO_SCHEDULE_COVER_DAYS)

    sold_upcs = set(sales["upc"].astype(str).str.strip().unique()) if sales is not None and not sales.empty else set()
    inv_sold = inv[inv["upc"].isin(sold_upcs)] if sold_upcs else inv.head(0)

    rop_vals: list[int] = []
    ss_vals: list[int] = []
    if sales is not None and not sales.empty and not inv_sold.empty:
        sales_by_upc = {k: g for k, g in sales.groupby("upc")}
        for _, row in inv_sold.iterrows():
            upc = row["upc"]
            sku_sales = sales_by_upc.get(upc, pd.DataFrame())
            math = compute_pos_ai_min(sku_sales, float(row["vendor_lead_time"]))
            rop_vals.append(int(math["ai_min"]))
            ss_vals.append(int(math["safety_stock"]))

    oos = int((inv["QuantityOnHand"] <= 0).sum())
    n = max(len(inv), 1)
    snap = {
        "store_units_on_hand": float(inv["QuantityOnHand"].sum()),
        "oos_sku_count": float(oos),
        "oos_rate": round(oos / n * 100, 2),
        "avg_vendor_lead_time": float(inv["vendor_lead_time"].mean()),
        "avg_reorder_point": float(np.mean(rop_vals)) if rop_vals else 0.0,
        "avg_safety_stock": float(np.mean(ss_vals)) if ss_vals else 0.0,
    }
    out = daily.copy()
    for k, v in snap.items():
        out[k] = v
    return out


def build_attribute_guide() -> pd.DataFrame:
    """Plain-English attribute dictionary for stakeholders."""
    return pd.DataFrame(ATTRIBUTE_GUIDE_ROWS)


def build_sample_rows(daily: pd.DataFrame, *, n: int = 6) -> pd.DataFrame:
    """First N days with readable column names for sharing with team lead."""
    if daily.empty:
        return pd.DataFrame()
    cols = [c for c in HEATMAP_FEATURE_COLUMNS if c in daily.columns]
    sample = daily[cols].head(n).copy()
    sample["date"] = pd.to_datetime(daily["date"]).head(n).dt.strftime("%Y-%m-%d").values
    ordered = ["date"] + [c for c in cols if c != "date"]
    sample = sample[ordered]
    return sample.rename(columns=HEATMAP_FEATURE_COLUMNS)


def build_store_daily_feature_frame(
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
) -> pd.DataFrame:
    """Store-level daily frame for correlation EDA (strong + cleaned candidate attrs)."""
    daily = aggregate_store_daily_from_sales(sales)
    if daily.empty:
        return daily
    daily = add_retail_calendar_columns(daily)
    daily = add_sales_lag_features(daily)
    # Skip static inventory snapshot + store-level SBC + weather on daily frame:
    # they are constant or weakly correlated and slow EDA without helping training.
    return daily.sort_values("date").reset_index(drop=True)


# ML model feature names (SKU-day panel) — matches FEATURE_COLS
ML_FEATURE_LABELS: dict[str, str] = {
    "is_weekend": "Weekend",
    "lag_1": "Yesterday sales",
    "lag_7": "Sales 7 days ago",
    "lag_14": "Sales 14 days ago",
    "lag_28": "Sales 28 days ago",
    "rolling_7": "Rolling 7-day avg",
    "rolling_14": "Rolling 14-day avg",
    "rolling_30": "Rolling 30-day avg",
    "rolling_std_7": "Sales volatility (7d)",
    "rolling_std_30": "Sales volatility (30d)",
    "current_stock": "Inventory on hand",
    "is_out_of_stock": "Out of stock flag",
    "unit_price": "Unit price ($)",
    "discount_pct": "Discount %",
    "vendor_lead_time": "Vendor lead time",
    "reorder_point": "Reorder point",
    "safety_stock": "Safety stock",
    "week_of_year": "Week of year",
}
