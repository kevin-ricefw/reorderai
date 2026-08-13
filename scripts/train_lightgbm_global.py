"""
Train global LightGBM on forecasting.daily_product_data (Kevin / ChatGPT panel).

- One model across all products
- Time-based validation (last 28 days held out)
- Saves booster + metrics under data/models/
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

import numpy as np
import pandas as pd

OUT_DIR = ROOT / "data" / "models"
VALID_DAYS = 28
HORIZON_REPORT = 14


def load_panel() -> pd.DataFrame:
    csv_path = Path.home() / "Desktop" / "forecasting_daily_product_data.csv"
    if csv_path.exists():
        print("loading CSV", csv_path)
        df = pd.read_csv(csv_path, low_memory=False)
    else:
        print("loading from DB forecasting.daily_product_data")
        from database.connectors.wecomm import WecommDatabaseConnector

        db = WecommDatabaseConnector()
        df = db.read_sql(
            """
            SELECT sale_date, product_id, upc, sku, product_name, category_id,
                   list_price, purchase_price, is_scale, pack_size,
                   target_sales, dow, is_weekend
            FROM forecasting.daily_product_data
            """
        )
    df["sale_date"] = pd.to_datetime(df["sale_date"]).dt.normalize()
    df["product_id"] = df["product_id"].astype(str)
    df["target_sales"] = pd.to_numeric(df["target_sales"], errors="coerce").fillna(0.0)
    df["list_price"] = pd.to_numeric(df.get("list_price"), errors="coerce").fillna(0.0)
    df["pack_size"] = pd.to_numeric(df.get("pack_size"), errors="coerce").fillna(1.0)
    if "dow" not in df.columns or df["dow"].isna().all():
        df["dow"] = df["sale_date"].dt.dayofweek
    if "is_weekend" not in df.columns:
        df["is_weekend"] = df["dow"].isin([5, 6]).astype(int)
    else:
        df["is_weekend"] = df["is_weekend"].astype(int)
    df["month"] = df["sale_date"].dt.month
    return df.sort_values(["product_id", "sale_date"]).reset_index(drop=True)


def add_lag_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("product_id", sort=False)["target_sales"]
    df = df.copy()
    df["lag_1"] = g.shift(1)
    df["lag_7"] = g.shift(7)
    df["lag_14"] = g.shift(14)
    df["lag_28"] = g.shift(28)
    df["roll_mean_7"] = g.transform(lambda s: s.shift(1).rolling(7, min_periods=1).mean())
    df["roll_mean_28"] = g.transform(lambda s: s.shift(1).rolling(28, min_periods=1).mean())
    df["roll_std_7"] = g.transform(lambda s: s.shift(1).rolling(7, min_periods=2).std())
    df["roll_std_7"] = df["roll_std_7"].fillna(0.0)
    # drop early rows without lag_28
    before = len(df)
    df = df.dropna(subset=["lag_1", "lag_7", "lag_14", "lag_28"]).reset_index(drop=True)
    print(f"feature rows: {before} -> {len(df)} after lag drop")
    return df


FEATURE_COLS = [
    "dow",
    "is_weekend",
    "month",
    "list_price",
    "pack_size",
    "lag_1",
    "lag_7",
    "lag_14",
    "lag_28",
    "roll_mean_7",
    "roll_mean_28",
    "roll_std_7",
]


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))
    # wape on days with any sales mass
    denom = float(np.sum(np.abs(y_true)))
    wape = float(np.sum(np.abs(err)) / denom) if denom > 0 else None
    # bias
    bias = float(np.mean(err))
    return {"mae": round(mae, 4), "rmse": round(rmse, 4), "wape": round(wape, 4) if wape is not None else None, "bias": round(bias, 4)}


def main() -> int:
    import lightgbm as lgb

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = load_panel()
    print("panel", len(df), "products", df["product_id"].nunique(),
          "dates", df["sale_date"].min().date(), "->", df["sale_date"].max().date())

    feat = add_lag_features(df)
    max_date = feat["sale_date"].max()
    cutoff = max_date - pd.Timedelta(days=VALID_DAYS)
    train = feat[feat["sale_date"] <= cutoff]
    valid = feat[feat["sale_date"] > cutoff]
    print("train rows", len(train), "valid rows", len(valid), "cutoff", cutoff.date())

    X_train = train[FEATURE_COLS]
    y_train = train["target_sales"]
    X_valid = valid[FEATURE_COLS]
    y_valid = valid["target_sales"]

    train_set = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_COLS)
    valid_set = lgb.Dataset(X_valid, label=y_valid, reference=train_set, feature_name=FEATURE_COLS)

    params = {
        "objective": "regression",
        "metric": ["l1", "l2"],
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_data_in_leaf": 50,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "verbosity": -1,
        "seed": 42,
    }

    print("training LightGBM...")
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=500,
        valid_sets=[train_set, valid_set],
        valid_names=["train", "valid"],
        callbacks=[
            lgb.early_stopping(50, verbose=True),
            lgb.log_evaluation(50),
        ],
    )

    pred_valid = booster.predict(X_valid, num_iteration=booster.best_iteration)
    pred_valid = np.maximum(pred_valid, 0.0)
    valid_metrics = metrics(y_valid.to_numpy(), pred_valid)

    # also overall train metrics (in-sample, for reference)
    pred_train = np.maximum(booster.predict(X_train, num_iteration=booster.best_iteration), 0.0)
    train_metrics = metrics(y_train.to_numpy(), pred_train)

    # product-level WAPE on validation (top movers)
    tmp = valid[["product_id", "product_name", "sale_date", "target_sales"]].copy()
    if "product_name" not in tmp.columns:
        tmp["product_name"] = tmp["product_id"]
    tmp["pred"] = pred_valid
    by_sku = (
        tmp.groupby("product_id", as_index=False)
        .agg(
            product_name=("product_name", "first"),
            actual=("target_sales", "sum"),
            pred=("pred", "sum"),
        )
    )
    by_sku["abs_err"] = (by_sku["pred"] - by_sku["actual"]).abs()
    top = by_sku.sort_values("actual", ascending=False).head(15).copy()
    top["wape"] = np.where(top["actual"] > 0, top["abs_err"] / top["actual"], np.nan)

    model_path = OUT_DIR / "lightgbm_global_daily.txt"
    booster.save_model(str(model_path))

    importance = sorted(
        zip(FEATURE_COLS, booster.feature_importance(importance_type="gain").tolist()),
        key=lambda x: x[1],
        reverse=True,
    )

    report = {
        "trained_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "forecasting.daily_product_data / Desktop CSV",
        "date_range": {
            "min": str(df["sale_date"].min().date()),
            "max": str(df["sale_date"].max().date()),
        },
        "panel_rows": int(len(df)),
        "feature_rows": int(len(feat)),
        "products": int(df["product_id"].nunique()),
        "valid_days": VALID_DAYS,
        "best_iteration": int(booster.best_iteration or 0),
        "features": FEATURE_COLS,
        "train_metrics": train_metrics,
        "valid_metrics": valid_metrics,
        "feature_importance_gain": [{"feature": f, "gain": float(g)} for f, g in importance],
        "top_skus_valid": top.round(4).to_dict(orient="records"),
        "model_path": str(model_path),
    }
    metrics_path = OUT_DIR / "lightgbm_global_metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    pred_path = OUT_DIR / "lightgbm_valid_predictions_sample.csv"
    sample = tmp.sort_values(["product_id", "sale_date"]).head(5000)
    sample.to_csv(pred_path, index=False)

    print("\n=== RESULTS ===")
    print("best_iteration", report["best_iteration"])
    print("train", train_metrics)
    print("valid", valid_metrics)
    print("top features", importance[:8])
    print("saved model", model_path)
    print("saved metrics", metrics_path)
    print("saved preds sample", pred_path)
    print("\nTop movers (valid window) actual vs pred:")
    print(top[["product_id", "product_name", "actual", "pred", "wape"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
