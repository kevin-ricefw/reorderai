"""
Nightly forecast batch (Design Decision 5 / §5.2).

  python scripts/run_nightly_forecast.py
  python scripts/run_nightly_forecast.py --lookback-days 0      # all history (default)
  python scripts/run_nightly_forecast.py --lookback-days 730    # last 2 years only

Writes P50/P90 for horizons 7/14/21/30/45 into data/forecast_store/.
Also learns per-SKU weekend/festival uplift from full history + India/US calendar.
Detect-order API only READS those files (no live model call).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import os

from dotenv import load_dotenv

from config.settings import PROJECT_ROOT, get_settings
from v2.forecasting.forecast_store_io import save_forecast_store
from v2.forecasting.pipeline import run_forecast_pipeline
from v2.forecasting.sales_loader import load_daily_demand, load_item_categories
from v2.forecasting.sku_uplift import learn_sku_uplift_table, sku_uplift_enabled, summarize_sku_uplift
from v2.forecasting.uplift import uplift_enabled


def main() -> int:
    parser = argparse.ArgumentParser(description="Wecomm nightly forecast batch")
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="History window in days; 0 = all available sales (default from env or 0)",
    )
    parser.add_argument("--weather-hot", action="store_true", help="Category weather flag")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    get_settings.cache_clear()

    if args.lookback_days is None:
        args.lookback_days = int(os.getenv("FORECAST_LOOKBACK_DAYS", "0"))

    lb_label = "ALL" if args.lookback_days <= 0 else f"{args.lookback_days}d"
    print(f"Loading daily demand (lookback={lb_label})...")
    print("  source preference: local Product Sales CSVs -> ai_pos_daily_sales -> Paul orders")
    daily = load_daily_demand(lookback_days=args.lookback_days)
    print(f"  rows={len(daily)}  skus={daily['item_id'].nunique() if not daily.empty else 0}")
    if not daily.empty:
        print(
            f"  date_span={daily['date'].min().date()} -> {daily['date'].max().date()}"
        )
    cats = load_item_categories()
    print(
        f"  categories loaded={len(cats)}  "
        f"sku_uplift={sku_uplift_enabled()}  category_uplift={uplift_enabled()}"
    )

    if sku_uplift_enabled() and not daily.empty:
        table = learn_sku_uplift_table(daily)
        print(f"  sku_uplift_learned={summarize_sku_uplift(table)}")

    result = run_forecast_pipeline(
        daily,
        item_category=cats,
        weather_hot=bool(args.weather_hot),
    )

    classifications = result["classifications"]
    forecasts = result["forecasts"]
    as_of = str(result["as_of"])

    out = save_forecast_store(classifications, forecasts, as_of=as_of)
    print(f"as_of={as_of}")
    if not classifications.empty:
        print(classifications["demand_class"].value_counts().to_string())
    if not forecasts.empty and "uplift_multiplier" in forecasts.columns:
        lifted = forecasts[forecasts["uplift_multiplier"] > 1.0]
        print(f"forecast rows={len(forecasts)}  rows_with_uplift={len(lifted)}")
    else:
        print(f"forecast rows={len(forecasts)}")
    print(f"wrote -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
