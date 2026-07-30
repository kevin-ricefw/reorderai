"""
Full SKU sales analysis: metrics, rankings, XGBoost/LightGBM forecasts, reorder recommendations.

Date range is taken from uploaded sales files unless start/end are passed explicitly.

Run:
  python scripts/run_sku_analysis.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.dashboard.pos_data_service import load_inventory, load_sales_detailed
from v2.analytics.dashboard_constants import DASHBOARD_NOTE
from v2.analytics.produce_filter import filter_dataframe_excluding_produce
from v2.analytics.sku_reorder_recommendations import compute_reorder_for_skus, merge_rankings_and_reorder
from v2.analytics.sku_sales_metrics import (
    ANALYSIS_END,
    ANALYSIS_START,
    build_ranking_table,
    compute_sku_sales_metrics,
    rank_skus,
)
from v2.forecasting.model_comparison import best_model_summary, compare_all_models
from v2.forecasting.xgb_demand_model import (
    TARGET_HORIZONS,
    build_sku_day_panel,
    forecast_all_skus,
    train_multi_horizon_models,
)

OUTPUT_DIR = ROOT / "outputs" / "analytics"


def resolve_analysis_window(
    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[date, date]:
    """Prefer explicit dates, else scan sales files, else fall back to module defaults."""
    if start_date is not None and end_date is not None:
        return start_date, end_date

    try:
        from api.services.upload_service import detect_sales_date_range

        detected = detect_sales_date_range()
    except Exception:
        detected = None

    if detected:
        return start_date or detected[0], end_date or detected[1]
    return start_date or ANALYSIS_START, end_date or ANALYSIS_END


def run_full_analysis(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Run metrics → model bake-off → multi-horizon train → forecasts → reorder CSVs.

    Returns the analysis_summary dict (also written to analysis_summary.json).
    """
    analysis_start, analysis_end = resolve_analysis_window(start_date, end_date)
    out = output_dir or OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    print(f"Loading sales {analysis_start} to {analysis_end}...")
    sales = load_sales_detailed(start_date=analysis_start, end_date=analysis_end)
    inventory = load_inventory()

    if sales.empty:
        raise ValueError(
            f"No sales rows found between {analysis_start} and {analysis_end}. "
            "Upload Product Sales CSVs first."
        )

    print(f"  Sales rows: {len(sales):,}  |  Unique SKUs sold: {sales['upc'].nunique():,}")

    sales_before = len(sales)
    sales = filter_dataframe_excluding_produce(
        sales, name_col="description", upc_col="upc", inventory=inventory
    )
    excluded = sales_before - len(sales)
    print(f"  Excluded loose produce rows: {excluded:,}  |  Remaining: {len(sales):,}")

    print("Computing SKU metrics and rankings...")
    metrics = compute_sku_sales_metrics(
        sales, inventory, start_date=analysis_start, end_date=analysis_end
    )
    ranked = rank_skus(metrics)
    ranking_table = build_ranking_table(metrics)

    metrics.to_csv(out / "sku_sales_metrics.csv", index=False)
    ranking_table.to_csv(out / "sku_rankings_top100.csv", index=False)

    top100 = ranking_table[ranking_table["IsTop100"] == "Yes"]
    top100.to_csv(out / "top_100_products.csv", index=False)

    print(f"  SKUs analyzed: {len(metrics):,}")
    print(f"  Top 100 saved: {len(top100)}")

    print("Building SKU-day feature panel (this may take a few minutes)...")
    panel = build_sku_day_panel(sales, inventory, start_date=analysis_start, end_date=analysis_end)
    print(f"  Panel rows: {len(panel):,}")

    print("Benchmarking LightGBM vs Random Forest vs XGBoost...")
    model_comparison = compare_all_models(panel, test_days=30)
    model_comparison.to_csv(out / "model_comparison.csv", index=False)
    best_model_summary(model_comparison).to_csv(
        out / "model_comparison_winners.csv", index=False
    )
    for horizon in TARGET_HORIZONS:
        subset = model_comparison[model_comparison["horizon_days"] == horizon]
        if subset.empty:
            continue
        winner = subset.loc[subset["r2"].idxmax()]
        print(
            f"  {horizon}d winner: {winner['model']}  R²={winner['r2']:.3f}  "
            f"RMSE={winner['rmse']:.2f}"
        )

    print("Training production demand models (7/14/30-day horizons)...")
    models, eval_results, model_name = train_multi_horizon_models(panel, test_days=30)

    eval_rows = []
    for h, res in eval_results.items():
        row = {"horizon_days": h, "model": res.model_name, **res.metrics}
        eval_rows.append(row)
        res.feature_importance.to_csv(out / f"feature_importance_{h}d.csv", index=False)
    eval_df = pd.DataFrame(eval_rows)
    eval_df.to_csv(out / "model_evaluation.csv", index=False)

    avg_r2 = eval_df["r2"].mean()
    print(f"  Model: {model_name}")
    for _, row in eval_df.iterrows():
        print(
            f"  {int(row['horizon_days'])}-day  RMSE={row['rmse']:.2f}  "
            f"MAE={row['mae']:.2f}  MAPE={row['mape']:.1f}%  R2={row['r2']:.3f}"
        )

    print("Generating forecasts for all SKUs...")
    forecasts = forecast_all_skus(panel, models, forecast_from=analysis_end)
    forecasts.to_csv(out / "sku_demand_forecasts.csv", index=False)

    print("Computing reorder recommendations...")
    reorder = compute_reorder_for_skus(
        ranked,
        sales,
        forecasts,
        confidence_from_r2=float(avg_r2),
    )
    reorder.to_csv(out / "sku_reorder_recommendations.csv", index=False)

    master = merge_rankings_and_reorder(ranking_table, reorder)
    master.to_csv(out / "sku_master_analysis.csv", index=False)

    order_now = reorder[reorder["order_now"] == "Yes"]
    order_now.to_csv(out / "order_now_list.csv", index=False)

    summary: dict[str, Any] = {
        "analysis_period": f"{analysis_start} to {analysis_end}",
        "analysis_start": analysis_start.isoformat(),
        "analysis_end": analysis_end.isoformat(),
        "calendar_days": (analysis_end - analysis_start).days + 1,
        "total_skus_sold": int(metrics["upc"].nunique()),
        "total_units_sold": float(metrics["total_quantity"].sum()),
        "total_revenue": round(float(metrics["total_revenue"].sum()), 2),
        "top100_count": int(top100.shape[0]),
        "model": model_name,
        "model_evaluation": eval_rows,
        "order_now_count": int(len(order_now)),
        "loose_produce_excluded_sales_rows": excluded,
        "produce_filter_note": DASHBOARD_NOTE,
        "model_comparison": model_comparison.to_dict(orient="records"),
        "outputs": [
            "sku_sales_metrics.csv",
            "sku_rankings_top100.csv",
            "top_100_products.csv",
            "model_comparison.csv",
            "model_comparison_winners.csv",
            "model_evaluation.csv",
            "sku_demand_forecasts.csv",
            "sku_reorder_recommendations.csv",
            "sku_master_analysis.csv",
            "order_now_list.csv",
        ],
        "note": (
            "Production forecasts use LightGBM (XGBoost fallback). "
            "See model_comparison.csv for LightGBM vs Random Forest vs XGBoost benchmark."
        ),
    }
    (out / "analysis_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\nDone. Outputs in: {out}")
    print(f"  Order Now SKUs: {len(order_now):,}")
    print(f"  Master file: sku_master_analysis.csv")
    return summary


def main() -> None:
    run_full_analysis()


if __name__ == "__main__":
    main()
