"""Compare demand forecast regressors on the same SKU-day holdout."""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from v2.forecasting.xgb_demand_model import FEATURE_COLS, TARGET_HORIZONS

MODEL_NAMES = ("LightGBM", "RandomForest", "XGBoost")


def _mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    mask = y_true > 0
    if not mask.any():
        return 0.0
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100)


def _make_regressor(model_name: str) -> Any:
    if model_name == "LightGBM":
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
        )
    if model_name == "RandomForest":
        return RandomForestRegressor(
            n_estimators=100,
            max_depth=12,
            min_samples_leaf=5,
            n_jobs=-1,
            random_state=42,
        )
    if model_name == "XGBoost":
        from xgboost import XGBRegressor

        return XGBRegressor(
            n_estimators=200,
            learning_rate=0.05,
            max_depth=8,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            verbosity=0,
        )
    raise ValueError(f"Unknown model: {model_name}")


def evaluate_model_on_holdout(
    panel: pd.DataFrame,
    *,
    model_name: str,
    horizon_days: int,
    test_days: int = 30,
) -> dict[str, Any]:
    """Train one model on time holdout and return metrics + elapsed seconds."""
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

    model = _make_regressor(model_name)
    start = time.perf_counter()
    model.fit(X_train, y_train)
    elapsed = time.perf_counter() - start

    preds = np.maximum(model.predict(X_test), 0)
    return {
        "horizon_days": horizon_days,
        "model": model_name,
        "rmse": float(np.sqrt(mean_squared_error(y_test, preds))),
        "mae": float(mean_absolute_error(y_test, preds)),
        "mape": _mape(y_test.values, preds),
        "r2": float(r2_score(y_test, preds)) if len(y_test) > 1 else 0.0,
        "train_rows": len(train),
        "test_rows": len(test),
        "train_seconds": round(elapsed, 2),
    }


def compare_all_models(
    panel: pd.DataFrame,
    *,
    test_days: int = 30,
    models: tuple[str, ...] = MODEL_NAMES,
) -> pd.DataFrame:
    """Run every model on every horizon; mark best by R² per horizon."""
    rows: list[dict[str, Any]] = []
    for horizon in TARGET_HORIZONS:
        for model_name in models:
            try:
                rows.append(
                    evaluate_model_on_holdout(
                        panel,
                        model_name=model_name,
                        horizon_days=horizon,
                        test_days=test_days,
                    )
                )
            except ImportError as exc:
                rows.append(
                    {
                        "horizon_days": horizon,
                        "model": model_name,
                        "rmse": None,
                        "mae": None,
                        "mape": None,
                        "r2": None,
                        "train_rows": 0,
                        "test_rows": 0,
                        "train_seconds": 0,
                        "error": str(exc),
                    }
                )

    df = pd.DataFrame(rows)
    if df.empty or "r2" not in df.columns:
        return df

    df["is_best_r2"] = False
    for horizon in TARGET_HORIZONS:
        subset = df[(df["horizon_days"] == horizon) & df["r2"].notna()]
        if subset.empty:
            continue
        best_idx = subset["r2"].idxmax()
        df.loc[best_idx, "is_best_r2"] = True

    return df.sort_values(["horizon_days", "r2"], ascending=[True, False]).reset_index(drop=True)


def best_model_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    """One row per horizon — winner by highest R²."""
    if comparison.empty:
        return comparison
    winners = comparison[comparison["is_best_r2"] == True].copy()  # noqa: E712
    return winners.reset_index(drop=True)
