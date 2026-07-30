"""XGBoost / LightGBM demand forecasting for POS SKU sales."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from v2.analytics.retail_features import add_retail_calendar_columns
from v2.forecasting.calendar_enrichment import merge_calendar_features

FEATURE_COLS = [
    # Strong / useful predictors only (weak calendar flags & on_promotion removed)
    "week_of_year",
    "is_weekend",
    "discount_pct",
    "unit_price",
    "lag_1",
    "lag_7",
    "lag_14",
    "lag_28",
    "rolling_7",
    "rolling_14",
    "rolling_30",
    "rolling_std_7",
    "rolling_std_30",
    "current_stock",
    "is_out_of_stock",
    "vendor_lead_time",
    "reorder_point",
    "safety_stock",
]

TARGET_HORIZONS = (7, 14, 30)


@dataclass
class DemandModelResult:
    model_name: str
    metrics: dict[str, float]
    feature_importance: pd.DataFrame
    predictions: pd.DataFrame = field(default_factory=pd.DataFrame)


def _pick_regressor():
    try:
        import lightgbm as lgb

        return lgb.LGBMRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=8,
            num_leaves=63,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbose=-1,
        ), "LightGBM"
    except ImportError:
        pass
    from xgboost import XGBRegressor

    return (
        XGBRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=8,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        ),
        "XGBoost",
    )


def _sku_reorder_metrics(sales: pd.DataFrame, inventory: pd.DataFrame) -> pd.DataFrame:
    """Precompute ROP and safety stock per UPC from sales history."""
    from app.dashboard.pos_reorder_math import compute_pos_ai_min
    from app.dashboard.vendor_catalog_loader import (
        DEFAULT_NO_SCHEDULE_COVER_DAYS,
        load_delivery_schedule,
        resolve_planning_cover_days,
    )

    inv = inventory.copy()
    inv["upc"] = inv["upc"].astype(str).str.strip()
    sold_upcs = set(sales["upc"].astype(str).str.strip().unique()) if not sales.empty else set()
    if sold_upcs:
        inv = inv[inv["upc"].isin(sold_upcs)]
    schedule = load_delivery_schedule()
    vendor_lead: dict[str, int] = {}
    for name in inv["vendor_name"].dropna().astype(str).unique():
        lead, _ = resolve_planning_cover_days(name, schedule)
        vendor_lead[name] = lead
    inv["vendor_lead_time"] = (
        inv["vendor_name"].astype(str).map(vendor_lead).fillna(DEFAULT_NO_SCHEDULE_COVER_DAYS).astype(int)
    )

    sales_by_upc = {k: g for k, g in sales.groupby("upc")} if not sales.empty else {}
    rop_vals: list[int] = []
    ss_vals: list[int] = []
    for _, row in inv.iterrows():
        upc = row["upc"]
        sku_sales = sales_by_upc.get(upc, pd.DataFrame())
        math = compute_pos_ai_min(sku_sales, float(row["vendor_lead_time"]))
        rop_vals.append(int(math["ai_min"]))
        ss_vals.append(int(math["safety_stock"]))

    inv["reorder_point"] = rop_vals
    inv["safety_stock"] = ss_vals
    inv["current_stock"] = pd.to_numeric(inv.get("QuantityOnHand"), errors="coerce").fillna(0)
    inv["unit_price"] = pd.to_numeric(inv.get("normal_price"), errors="coerce").fillna(0)
    inv["is_out_of_stock"] = (inv["current_stock"] <= 0).astype(int)
    return inv.rename(columns={"unit_price": "inv_unit_price"})[
        [
            "upc",
            "vendor_lead_time",
            "current_stock",
            "is_out_of_stock",
            "inv_unit_price",
            "reorder_point",
            "safety_stock",
        ]
    ]


def build_sku_day_panel(
    sales: pd.DataFrame,
    inventory: pd.DataFrame,
    *,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Complete SKU × day panel with zero-filled non-sale days."""
    sales = sales.copy()
    sales["date"] = pd.to_datetime(sales["date"]).dt.normalize()
    sales["upc"] = sales["upc"].astype(str).str.strip()

    dates = pd.date_range(start_date, end_date, freq="D")
    skus = sorted(sales["upc"].unique())

    grid = pd.MultiIndex.from_product([dates, skus], names=["date", "upc"])
    panel = pd.DataFrame(index=grid).reset_index()

    agg_cols: dict[str, tuple[str, str]] = {
        "quantity": ("quantity", "sum"),
        "revenue": ("revenue", "sum"),
        "on_promotion": ("on_promotion", "max"),
    }
    if "discount_pct" in sales.columns:
        agg_cols["discount_pct"] = ("discount_pct", "mean")
    if "list_price" in sales.columns:
        agg_cols["list_price"] = ("list_price", "max")

    agg = sales.groupby(["date", "upc"], as_index=False).agg(**agg_cols)
    panel = panel.merge(agg, on=["date", "upc"], how="left")
    panel["quantity"] = panel["quantity"].fillna(0)
    panel["revenue"] = panel["revenue"].fillna(0)
    panel["on_promotion"] = panel["on_promotion"].fillna(False).astype(int)
    panel["discount_pct"] = panel.get("discount_pct", pd.Series(0, index=panel.index)).fillna(0)
    if "list_price" in panel.columns:
        panel["unit_price"] = panel["list_price"].fillna(0)
    else:
        panel["unit_price"] = np.where(panel["quantity"] > 0, panel["revenue"] / panel["quantity"], 0)

    sku_inv = _sku_reorder_metrics(sales, inventory)
    panel = panel.merge(sku_inv, on="upc", how="left")
    panel["vendor_lead_time"] = panel["vendor_lead_time"].fillna(14).astype(int)
    panel["current_stock"] = panel["current_stock"].fillna(0)
    panel["is_out_of_stock"] = panel["is_out_of_stock"].fillna(0).astype(int)
    panel["reorder_point"] = panel["reorder_point"].fillna(0)
    panel["safety_stock"] = panel["safety_stock"].fillna(0)
    panel["unit_price"] = np.where(
        panel["unit_price"] > 0,
        panel["unit_price"],
        panel["inv_unit_price"].fillna(0),
    )
    panel = panel.drop(columns=["inv_unit_price"], errors="ignore")

    panel = merge_calendar_features(panel, "date")
    panel["is_festival"] = panel["indian_festival"].notna().astype(int)
    panel["week_of_year"] = panel["date"].dt.isocalendar().week.astype(int)
    panel["is_weekend"] = panel["is_weekend"].astype(int)

    cal_daily = add_retail_calendar_columns(panel[["date"]].drop_duplicates())
    panel = panel.merge(
        cal_daily[
            [
                "date",
                "is_school_break",
                "is_month_end",
                "is_payday_window",
            ]
        ],
        on="date",
        how="left",
    )
    for col in ("is_school_break", "is_month_end", "is_payday_window"):
        panel[col] = panel[col].fillna(0).astype(int)

    panel = panel.sort_values(["upc", "date"]).reset_index(drop=True)
    g = panel.groupby("upc")["quantity"]
    panel["lag_1"] = g.shift(1).fillna(0)
    panel["lag_7"] = g.shift(7).fillna(0)
    panel["lag_14"] = g.shift(14).fillna(0)
    panel["lag_28"] = g.shift(28).fillna(0)
    panel["rolling_7"] = g.transform(lambda s: s.shift(1).rolling(7, min_periods=1).mean()).fillna(0)
    panel["rolling_14"] = g.transform(lambda s: s.shift(1).rolling(14, min_periods=1).mean()).fillna(0)
    panel["rolling_30"] = g.transform(lambda s: s.shift(1).rolling(30, min_periods=1).mean()).fillna(0)
    panel["rolling_std_7"] = g.transform(lambda s: s.shift(1).rolling(7, min_periods=2).std()).fillna(0)
    panel["rolling_std_30"] = g.transform(lambda s: s.shift(1).rolling(30, min_periods=2).std()).fillna(0)

    for h in TARGET_HORIZONS:
        panel[f"target_{h}d"] = g.transform(
            lambda s, horizon=h: s.shift(-1).rolling(horizon, min_periods=1).sum()
        )

    drop_cols = [c for c in ("us_holiday", "indian_festival", "day_type", "day_name", "day_of_week", "is_weekday", "is_long_weekend", "list_price") if c in panel.columns]
    return panel.drop(columns=drop_cols)


def _mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true > 0
    if not mask.any():
        return 0.0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def train_demand_model(
    panel: pd.DataFrame,
    *,
    horizon_days: int = 7,
    test_days: int = 30,
) -> DemandModelResult:
    """
    Train global SKU-day demand model with time-based holdout.

    Target: total quantity sold in next `horizon_days` days.
    """
    target_col = f"target_{horizon_days}d"
    df = panel.dropna(subset=[target_col]).copy()
    max_date = df["date"].max()
    split_date = max_date - pd.Timedelta(days=test_days)

    train = df[df["date"] <= split_date]
    test = df[df["date"] > split_date]

    X_train = train[FEATURE_COLS].fillna(0)
    y_train = train[target_col]
    X_test = test[FEATURE_COLS].fillna(0)
    y_test = test[target_col]

    model, model_name = _pick_regressor()
    model.fit(X_train, y_train)
    preds = np.maximum(model.predict(X_test), 0)

    metrics = {
        "rmse": float(np.sqrt(mean_squared_error(y_test, preds))),
        "mae": float(mean_absolute_error(y_test, preds)),
        "mape": _mape(y_test.values, preds),
        "r2": float(r2_score(y_test, preds)) if len(y_test) > 1 else 0.0,
        "horizon_days": horizon_days,
        "train_rows": len(train),
        "test_rows": len(test),
    }

    imp = pd.DataFrame({"feature": FEATURE_COLS, "importance": model.feature_importances_})
    imp = imp.sort_values("importance", ascending=False).reset_index(drop=True)

    pred_df = test[["date", "upc", target_col]].copy()
    pred_df["predicted"] = preds
    pred_df["horizon_days"] = horizon_days

    return DemandModelResult(model_name=model_name, metrics=metrics, feature_importance=imp, predictions=pred_df)


def forecast_all_skus(
    panel: pd.DataFrame,
    models: dict[int, Any],
    *,
    forecast_from: date | None = None,
) -> pd.DataFrame:
    """Generate 7/14/30-day demand forecasts for every SKU using last feature row."""
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    ref_date = pd.Timestamp(forecast_from) if forecast_from else panel["date"].max()
    last_rows = panel[panel["date"] == ref_date].copy()

    if last_rows.empty:
        return pd.DataFrame()

    result = last_rows[["upc"]].copy()
    result["forecast_date"] = ref_date.date()
    for horizon, model in models.items():
        X = last_rows[FEATURE_COLS].fillna(0)
        result[f"forecast_{horizon}d"] = np.maximum(model.predict(X), 0).round(2)

    return result.reset_index(drop=True)


def train_multi_horizon_models(
    panel: pd.DataFrame,
    *,
    test_days: int = 30,
) -> tuple[dict[int, Any], dict[int, DemandModelResult], str]:
    """Train models for 7, 14, and 30-day horizons."""
    models: dict[int, Any] = {}
    results: dict[int, DemandModelResult] = {}
    model_name = ""

    for h in TARGET_HORIZONS:
        res = train_demand_model(panel, horizon_days=h, test_days=test_days)
        results[h] = res
        model_name = res.model_name
        target_col = f"target_{h}d"
        df = panel.dropna(subset=[target_col])
        regressor, _ = _pick_regressor()
        regressor.fit(df[FEATURE_COLS].fillna(0), df[target_col])
        models[h] = regressor

    return models, results, model_name
