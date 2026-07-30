"""
Hybrid residual forecasting relay: Prophet (macro) → LightGBM (order-pattern residuals).

Invoices are ONLY a manual order/receive pattern signal (Received Date ± qty).
Expiry is never used.

  Phase 1  Prophet on ~6 months daily sales → yhat, yhat_upper
  Phase 2  Residual = Actual − Prophet yhat
  Phase 3  LightGBM predicts Residual from order-pattern features
  Phase 4  Hybrid demand = Prophet yhat + LightGBM residual̂   over (L + C)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from v2.forecasting.order_pattern_features import build_order_pattern_features

RESIDUAL_FEATURE_COLS = [
    "days_since_last_receipt",
    "last_receipt_gap_days",
    "receipts_last_7d",
    "qty_received_last_7d",
]


@dataclass
class HybridForecastResult:
    """Full relay outputs for history + future (L+C) window."""

    history: pd.DataFrame
    future: pd.DataFrame
    prophet_model: Any = None
    lgbm_model: Any = None
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def future_hybrid_demand(self) -> float:
        return float(self.future["hybrid_yhat"].sum()) if not self.future.empty else 0.0


def _require_prophet():
    try:
        from prophet import Prophet
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Prophet is required for the hybrid residual engine. "
            "Install with: pip install prophet"
        ) from exc
    return Prophet


def _require_lightgbm():
    try:
        import lightgbm as lgb
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "LightGBM is required for residual modeling. "
            "Install with: pip install lightgbm"
        ) from exc
    return lgb


def _normalize_sales(sales: pd.DataFrame) -> pd.DataFrame:
    df = sales.copy()
    rename = {}
    lower = {c.lower().strip(): c for c in df.columns}
    if "ds" not in df.columns:
        date_col = lower.get("date") or lower.get("ds")
        if date_col is None:
            raise ValueError("Sales frame needs a date / ds column.")
        rename[date_col] = "ds"
    if "y" not in df.columns:
        y_col = lower.get("sales") or lower.get("y") or lower.get("quantity")
        if y_col is None:
            raise ValueError("Sales frame needs a sales / y column.")
        rename[y_col] = "y"
    df = df.rename(columns=rename)
    df["ds"] = pd.to_datetime(df["ds"]).dt.normalize()
    df["y"] = pd.to_numeric(df["y"], errors="coerce").fillna(0.0)
    return df[["ds", "y"]].sort_values("ds").drop_duplicates("ds", keep="last")


def train_prophet_baseline(
    sales: pd.DataFrame,
    *,
    interval_width: float = 0.95,
) -> tuple[Any, pd.DataFrame]:
    """Phase 1 — structural baseline on continuous daily sales."""
    Prophet = _require_prophet()
    hist = _normalize_sales(sales)
    model = Prophet(
        interval_width=interval_width,
        daily_seasonality=False,
        weekly_seasonality=True,
        yearly_seasonality=False,
    )
    model.fit(hist)
    in_sample = model.predict(hist[["ds"]])
    merged = hist.merge(
        in_sample[["ds", "yhat", "yhat_lower", "yhat_upper"]],
        on="ds",
        how="left",
    )
    return model, merged


def compute_residuals(prophet_history: pd.DataFrame) -> pd.DataFrame:
    """Phase 2 — Residual = Actual − Prophet yhat."""
    out = prophet_history.copy()
    out["residual"] = out["y"] - out["yhat"]
    return out


def train_residual_lgbm(
    residual_frame: pd.DataFrame,
    order_log: pd.DataFrame,
    *,
    random_state: int = 42,
) -> tuple[Any, pd.DataFrame, dict[str, float]]:
    """
    Phase 3 — LightGBM predicts Residual from order-pattern features only.

    ``order_log`` = manual invoice/receive history (Received Date ± qty). No expiry.
    """
    lgb = _require_lightgbm()
    feats = build_order_pattern_features(residual_frame["ds"], order_log)
    train = residual_frame.merge(feats, on="ds", how="left")
    X = train[RESIDUAL_FEATURE_COLS]
    y = train["residual"]

    model = lgb.LGBMRegressor(
        n_estimators=200,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        subsample=0.9,
        colsample_bytree=0.9,
        random_state=random_state,
        verbose=-1,
    )
    model.fit(X, y)
    train["residual_hat"] = model.predict(X)
    train["hybrid_yhat"] = train["yhat"] + train["residual_hat"]

    err = train["residual"] - train["residual_hat"]
    metrics = {
        "residual_mae": float(np.mean(np.abs(err))),
        "residual_rmse": float(np.sqrt(np.mean(err**2))),
        "hybrid_mae": float(np.mean(np.abs(train["y"] - train["hybrid_yhat"]))),
        "prophet_mae": float(np.mean(np.abs(train["y"] - train["yhat"]))),
        "n_train_days": float(len(train)),
    }
    return model, train, metrics


def forecast_hybrid_window(
    prophet_model: Any,
    lgbm_model: Any,
    order_log: pd.DataFrame,
    *,
    last_history_date: pd.Timestamp,
    horizon_days: int,
) -> pd.DataFrame:
    """
    Phase 4 — future blend over horizon H = L + C.

    Final Expected Demand_t = Prophet yhat_t + LightGBM residual̂_t
    """
    last = pd.Timestamp(last_history_date).normalize()
    future_dates = pd.date_range(
        last + pd.Timedelta(days=1), periods=horizon_days, freq="D"
    )
    future_df = pd.DataFrame({"ds": future_dates})
    prophet_fc = prophet_model.predict(future_df)
    feats = build_order_pattern_features(future_dates, order_log)
    residual_hat = lgbm_model.predict(feats[RESIDUAL_FEATURE_COLS])

    out = prophet_fc[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    out = out.merge(feats, on="ds", how="left")
    out["residual_hat"] = residual_hat
    out["hybrid_yhat"] = (out["yhat"] + out["residual_hat"]).clip(lower=0.0)
    out["yhat"] = out["yhat"].clip(lower=0.0)
    out["yhat_upper"] = out["yhat_upper"].clip(lower=0.0)
    return out


def fit_hybrid_residual_engine(
    sales: pd.DataFrame,
    order_log: pd.DataFrame,
    *,
    lead_time_days: int,
    days_to_cover: int,
    interval_width: float = 0.95,
) -> HybridForecastResult:
    """
    End-to-end residual relay for dashboard toggles L and C.

    ``order_log`` = manual receive/order pattern (not expiry, not 3000-SKU stock).
    Future window length = L + C.
    """
    L = max(int(lead_time_days), 1)
    C = max(int(days_to_cover), 0)
    horizon = L + C

    prophet_model, prophet_hist = train_prophet_baseline(
        sales, interval_width=interval_width
    )
    with_resid = compute_residuals(prophet_hist)
    lgbm_model, history, metrics = train_residual_lgbm(with_resid, order_log)
    last_ds = history["ds"].max()
    future = forecast_hybrid_window(
        prophet_model,
        lgbm_model,
        order_log,
        last_history_date=last_ds,
        horizon_days=horizon,
    )
    return HybridForecastResult(
        history=history,
        future=future,
        prophet_model=prophet_model,
        lgbm_model=lgbm_model,
        metrics=metrics,
    )
