"""Syntetos-Boylan demand-pattern classification (ADI + CV²).

Classic cutoffs (Syntetos, Boylan & Croston, 2005):
  ADI threshold = 1.32
  CV² threshold = 0.49

| Class        | ADI        | CV²         | Meaning                                      |
|--------------|------------|-------------|----------------------------------------------|
| Smooth       | < 1.32     | ≤ 0.49      | Regular demand, stable size                  |
| Intermittent | ≥ 1.32     | ≤ 0.49      | Sporadic demand, stable size when it sells   |
| Erratic      | < 1.32     | > 0.49      | Frequent demand, highly variable size        |
| Lumpy        | ≥ 1.32     | > 0.49      | Rare demand, highly variable size            |
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

ADI_THRESHOLD = 1.32
CV2_THRESHOLD = 0.49

CLASS_SMOOTH = "Smooth"
CLASS_INTERMITTENT = "Intermittent"
CLASS_ERRATIC = "Erratic"
CLASS_LUMPY = "Lumpy"
CLASS_NO_DEMAND = "No demand"
CLASS_SINGLE_HIT = "Single demand day"  # only one non-zero day — ADI/CV² not fully defined

CLASS_ORDER = [
    CLASS_SMOOTH,
    CLASS_INTERMITTENT,
    CLASS_ERRATIC,
    CLASS_LUMPY,
    CLASS_SINGLE_HIT,
    CLASS_NO_DEMAND,
]

FORECAST_HINT = {
    CLASS_SMOOTH: "Standard time-series / LightGBM OK (ADS + ROP reliable)",
    CLASS_INTERMITTENT: "Prefer Croston / SBA / TSB; avoid plain ADS alone",
    CLASS_ERRATIC: "Use robust models; higher safety stock; review pack size",
    CLASS_LUMPY: "Hardest class — Croston/SBA + judgment; high safety buffer",
    CLASS_SINGLE_HIT: "Too little history — watchlist / manual review",
    CLASS_NO_DEMAND: "No sales in window — exclude or dormant SKU",
}


def classify_adi_cv2(adi: float, cv2: float) -> str:
    """Map ADI and CV² to a Syntetos-Boylan class."""
    if not np.isfinite(adi) or not np.isfinite(cv2):
        return CLASS_SINGLE_HIT
    frequent = adi < ADI_THRESHOLD
    stable = cv2 <= CV2_THRESHOLD
    if frequent and stable:
        return CLASS_SMOOTH
    if (not frequent) and stable:
        return CLASS_INTERMITTENT
    if frequent and (not stable):
        return CLASS_ERRATIC
    return CLASS_LUMPY


def _sku_demand_stats(
    daily_qty: pd.Series,
    *,
    calendar_days: int,
) -> dict:
    """
    Compute ADI / CV² from a daily quantity series (index = date, values include zeros).

    ADI = average number of calendar days between consecutive non-zero demand days.
    CV² = (σ/μ)² of non-zero demand sizes only.
    """
    qty = pd.to_numeric(daily_qty, errors="coerce").fillna(0.0)
    nonzero = qty[qty > 0]
    n_demand_days = int(len(nonzero))
    total_qty = float(qty.sum())

    if n_demand_days == 0:
        return {
            "demand_days": 0,
            "zero_days": int(calendar_days),
            "total_qty": 0.0,
            "mean_demand_size": np.nan,
            "std_demand_size": np.nan,
            "adi": np.nan,
            "cv2": np.nan,
            "demand_class": CLASS_NO_DEMAND,
            "adi_ratio": np.nan,
        }

    mean_size = float(nonzero.mean())
    std_size = float(nonzero.std(ddof=1)) if n_demand_days >= 2 else 0.0
    cv2 = float((std_size / mean_size) ** 2) if mean_size > 0 and n_demand_days >= 2 else 0.0

    # Ratio form (also common): periods / demand occurrences
    adi_ratio = float(calendar_days) / float(n_demand_days)

    if n_demand_days == 1:
        return {
            "demand_days": 1,
            "zero_days": int(calendar_days) - 1,
            "total_qty": total_qty,
            "mean_demand_size": mean_size,
            "std_demand_size": 0.0,
            "adi": float(calendar_days),  # only one hit in the window
            "cv2": 0.0,
            "demand_class": CLASS_SINGLE_HIT,
            "adi_ratio": adi_ratio,
        }

    # Interval ADI: mean gap (in days) between successive demand dates
    demand_dates = pd.to_datetime(nonzero.index).sort_values()
    intervals = demand_dates.to_series().diff().dt.days.dropna()
    adi = float(intervals.mean()) if len(intervals) else adi_ratio

    return {
        "demand_days": n_demand_days,
        "zero_days": int(calendar_days) - n_demand_days,
        "total_qty": total_qty,
        "mean_demand_size": mean_size,
        "std_demand_size": std_size,
        "adi": adi,
        "cv2": cv2,
        "demand_class": classify_adi_cv2(adi, cv2),
        "adi_ratio": adi_ratio,
    }


def classify_skus_syntetos_boylan(
    sales: pd.DataFrame,
    *,
    inventory: pd.DataFrame | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    upc_col: str = "upc",
    date_col: str = "date",
    qty_col: str = "quantity",
    desc_col: str = "description",
) -> pd.DataFrame:
    """
    Classify every SKU in ``sales`` using Syntetos-Boylan ADI + CV².

    Builds a full daily calendar (missing days = 0 demand) per SKU.
    """
    if sales is None or sales.empty:
        return pd.DataFrame()

    df = sales.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])
    df[upc_col] = df[upc_col].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df[qty_col] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0.0)

    if start_date is not None:
        df = df[df[date_col] >= pd.Timestamp(start_date)]
    if end_date is not None:
        df = df[df[date_col] <= pd.Timestamp(end_date)]
    if df.empty:
        return pd.DataFrame()

    start_ts = pd.Timestamp(start_date) if start_date else df[date_col].min().normalize()
    end_ts = pd.Timestamp(end_date) if end_date else df[date_col].max().normalize()
    calendar = pd.date_range(start_ts.normalize(), end_ts.normalize(), freq="D")
    calendar_days = int(len(calendar))

    # Aggregate to UPC × day
    daily = (
        df.groupby([upc_col, date_col], as_index=False)[qty_col]
        .sum()
        .rename(columns={qty_col: "quantity"})
    )

    # Best description per UPC
    if desc_col in df.columns:
        names = (
            df.sort_values(date_col)
            .groupby(upc_col, as_index=False)[desc_col]
            .last()
            .rename(columns={desc_col: "product_name"})
        )
    else:
        names = pd.DataFrame({upc_col: daily[upc_col].unique(), "product_name": ""})

    rows: list[dict] = []
    for upc, grp in daily.groupby(upc_col, sort=False):
        series = (
            grp.set_index(date_col)["quantity"]
            .groupby(level=0)
            .sum()
            .reindex(calendar, fill_value=0.0)
        )
        stats = _sku_demand_stats(series, calendar_days=calendar_days)
        rows.append({"upc": str(upc), **stats})

    out = pd.DataFrame(rows)
    out = out.merge(names, left_on="upc", right_on=upc_col, how="left")
    if upc_col != "upc" and upc_col in out.columns:
        out = out.drop(columns=[upc_col])

    # Inventory enrich (vendor, on-hand, pack)
    if inventory is not None and not inventory.empty:
        inv = inventory.copy()
        inv["upc"] = inv["upc"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        keep = ["upc"]
        rename = {}
        if "description" in inv.columns:
            keep.append("description")
            rename["description"] = "inv_description"
        if "vendor_name" in inv.columns:
            keep.append("vendor_name")
        if "QuantityOnHand" in inv.columns:
            keep.append("QuantityOnHand")
            rename["QuantityOnHand"] = "current_inventory"
        if "pack" in inv.columns:
            keep.append("pack")
        inv = inv[keep].drop_duplicates(subset=["upc"], keep="first").rename(columns=rename)
        out = out.merge(inv, on="upc", how="left")
        if "product_name" in out.columns and "inv_description" in out.columns:
            out["product_name"] = out["product_name"].fillna(out["inv_description"])
            out = out.drop(columns=["inv_description"], errors="ignore")

    out["forecast_hint"] = out["demand_class"].map(FORECAST_HINT).fillna("")
    out["adi_threshold"] = ADI_THRESHOLD
    out["cv2_threshold"] = CV2_THRESHOLD
    out["analysis_start"] = start_ts.date().isoformat()
    out["analysis_end"] = end_ts.date().isoformat()
    out["calendar_days"] = calendar_days

    # Readable column order
    preferred = [
        "upc",
        "product_name",
        "vendor_name",
        "demand_class",
        "adi",
        "cv2",
        "adi_ratio",
        "demand_days",
        "zero_days",
        "total_qty",
        "mean_demand_size",
        "std_demand_size",
        "current_inventory",
        "pack",
        "forecast_hint",
        "adi_threshold",
        "cv2_threshold",
        "analysis_start",
        "analysis_end",
        "calendar_days",
    ]
    cols = [c for c in preferred if c in out.columns] + [
        c for c in out.columns if c not in preferred
    ]
    class_rank = {c: i for i, c in enumerate(CLASS_ORDER)}
    out["_class_rank"] = out["demand_class"].map(class_rank).fillna(99)
    out = out[cols + ["_class_rank"]].sort_values(
        ["_class_rank", "adi", "cv2", "upc"], ascending=[True, True, True, True]
    )
    return out.drop(columns=["_class_rank"]).reset_index(drop=True)


def summarize_demand_classes(classified: pd.DataFrame) -> pd.DataFrame:
    """Counts, % of SKUs, and qty share by demand class."""
    if classified.empty:
        return pd.DataFrame()
    total_skus = len(classified)
    total_qty = float(classified["total_qty"].sum()) if "total_qty" in classified.columns else 0.0
    rows = []
    for cls in CLASS_ORDER:
        part = classified[classified["demand_class"] == cls]
        n = len(part)
        if n == 0:
            continue
        qty = float(part["total_qty"].sum()) if "total_qty" in part.columns else 0.0
        rows.append(
            {
                "demand_class": cls,
                "sku_count": n,
                "sku_pct": round(100.0 * n / total_skus, 2),
                "total_qty": qty,
                "qty_pct": round(100.0 * qty / total_qty, 2) if total_qty else 0.0,
                "avg_adi": round(float(part["adi"].mean()), 3) if part["adi"].notna().any() else np.nan,
                "avg_cv2": round(float(part["cv2"].mean()), 3) if part["cv2"].notna().any() else np.nan,
                "forecast_hint": FORECAST_HINT.get(cls, ""),
            }
        )
    return pd.DataFrame(rows)


def methodology_dataframe() -> pd.DataFrame:
    """Small reference table for the Excel Methodology sheet."""
    return pd.DataFrame(
        [
            {
                "metric": "ADI",
                "definition": "Average Demand Interval — mean calendar days between consecutive non-zero demand days",
                "threshold": ADI_THRESHOLD,
                "note": f"ADI < {ADI_THRESHOLD} = frequent; ADI ≥ {ADI_THRESHOLD} = infrequent",
            },
            {
                "metric": "CV²",
                "definition": "Squared coefficient of variation of non-zero demand sizes = (σ/μ)²",
                "threshold": CV2_THRESHOLD,
                "note": f"CV² ≤ {CV2_THRESHOLD} = stable size; CV² > {CV2_THRESHOLD} = volatile size",
            },
            {
                "metric": "Smooth",
                "definition": f"ADI < {ADI_THRESHOLD} AND CV² ≤ {CV2_THRESHOLD}",
                "threshold": "",
                "note": FORECAST_HINT[CLASS_SMOOTH],
            },
            {
                "metric": "Intermittent",
                "definition": f"ADI ≥ {ADI_THRESHOLD} AND CV² ≤ {CV2_THRESHOLD}",
                "threshold": "",
                "note": FORECAST_HINT[CLASS_INTERMITTENT],
            },
            {
                "metric": "Erratic",
                "definition": f"ADI < {ADI_THRESHOLD} AND CV² > {CV2_THRESHOLD}",
                "threshold": "",
                "note": FORECAST_HINT[CLASS_ERRATIC],
            },
            {
                "metric": "Lumpy",
                "definition": f"ADI ≥ {ADI_THRESHOLD} AND CV² > {CV2_THRESHOLD}",
                "threshold": "",
                "note": FORECAST_HINT[CLASS_LUMPY],
            },
            {
                "metric": "adi_ratio",
                "definition": "Alternate ADI = calendar_days / demand_days (reported for reference)",
                "threshold": "",
                "note": "Classification uses interval-based ADI, not adi_ratio",
            },
        ]
    )
