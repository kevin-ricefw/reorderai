"""
Smooth demand model — LightGBM pooled regression (Decision 1 / 4).

Falls back to bootstrap percentiles when LightGBM is unavailable or data is thin.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from v2.forecasting.monte_carlo import smooth_percentile_forecast


def _build_feature_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """daily: item_id, date, quantity → supervised rows for next-day demand."""
    frames: list[pd.DataFrame] = []
    for item_id, g in daily.groupby("item_id"):
        g = g.sort_values("date").copy()
        s = g.set_index("date")["quantity"].asfreq("D", fill_value=0.0)
        df = pd.DataFrame({"quantity": s})
        df["item_id"] = str(item_id)
        df["dow"] = df.index.dayofweek
        df["lag_1"] = df["quantity"].shift(1)
        df["lag_7"] = df["quantity"].shift(7)
        df["roll_7"] = df["quantity"].shift(1).rolling(7).mean()
        df["target"] = df["quantity"]
        frames.append(df.dropna().reset_index(drop=True))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def forecast_smooth_horizons(
    daily_series: pd.Series,
    *,
    horizons: list[int],
    item_id: str = "",
    pooled_model: Any | None = None,
) -> list[dict]:
    """
    If pooled_model is a fitted LightGBM booster, use recursive daily forecasts
    then take path uncertainty via residual scale for P90.
    Else bootstrap.
    """
    rows: list[dict] = []
    if pooled_model is None:
        for h in horizons:
            p50, p90 = smooth_percentile_forecast(daily_series, horizon_days=h)
            rows.append(
                {
                    "item_id": str(item_id),
                    "horizon_days": int(h),
                    "p50": p50,
                    "p90": p90,
                    "demand_class": "smooth",
                    "model": "smooth_bootstrap",
                }
            )
        return rows

    y = pd.to_numeric(daily_series, errors="coerce").fillna(0.0)
    hist = y.to_numpy(dtype=float)
    resid_std = float(np.std(np.diff(hist))) if len(hist) > 2 else float(np.std(hist) or 0.5)
    resid_std = max(resid_std, 0.1)

    for h in horizons:
        # Recursive point forecast
        window = list(hist[-14:]) if len(hist) else [0.0]
        preds: list[float] = []
        for step in range(int(h)):
            lag_1 = window[-1] if window else 0.0
            lag_7 = window[-7] if len(window) >= 7 else lag_1
            roll_7 = float(np.mean(window[-7:])) if window else 0.0
            # dow cycles from last known
            dow = int((y.index[-1] + pd.Timedelta(days=step + 1)).dayofweek) if len(y) else step % 7
            X = np.array([[dow, lag_1, lag_7, roll_7]], dtype=float)
            try:
                pred = float(pooled_model.predict(X)[0])
            except Exception:
                pred = lag_1
            pred = max(pred, 0.0)
            preds.append(pred)
            window.append(pred)
        p50 = float(sum(preds))
        # Normal-ish band on sum of independent-ish residuals
        p90 = p50 + 1.28 * resid_std * np.sqrt(max(h, 1))
        rows.append(
            {
                "item_id": str(item_id),
                "horizon_days": int(h),
                "p50": round(p50, 4),
                "p90": round(max(p90, p50), 4),
                "demand_class": "smooth",
                "model": "lightgbm",
            }
        )
    return rows


def fit_pooled_lightgbm(daily: pd.DataFrame, smooth_item_ids: list[str]) -> Any | None:
    """Fit one LightGBM on all Smooth SKUs. Returns model or None."""
    if len(smooth_item_ids) < 3:
        return None
    sub = daily[daily["item_id"].astype(str).isin({str(x) for x in smooth_item_ids})]
    feat = _build_feature_frame(sub)
    if len(feat) < 40:
        return None
    try:
        import lightgbm as lgb
    except ImportError:
        return None

    X = feat[["dow", "lag_1", "lag_7", "roll_7"]].to_numpy(dtype=float)
    y = feat["target"].to_numpy(dtype=float)
    train = lgb.Dataset(X, label=y)
    params = {
        "objective": "regression",
        "metric": "l2",
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_data_in_leaf": 5,
        "verbosity": -1,
    }
    return lgb.train(params, train, num_boost_round=80)
