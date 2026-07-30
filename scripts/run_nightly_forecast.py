"""
Nightly Phase-1 forecast batch (Design Decision 5 / §5.2).

  python scripts/run_nightly_forecast.py
  python scripts/run_nightly_forecast.py --lookback-days 180

Writes P50/P90 for horizons 7/14/21/30/45 into data/forecast_store/.
Detect-order API only READS those files (no live model call).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from config.settings import PROJECT_ROOT, get_settings
from v2.forecasting.forecast_store_io import save_forecast_store
from v2.forecasting.pipeline import run_forecast_pipeline
from v2.forecasting.sales_loader import load_daily_demand, load_item_categories
from v2.forecasting.uplift import uplift_enabled


def main() -> int:
    parser = argparse.ArgumentParser(description="Wecomm nightly forecast batch")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--weather-hot", action="store_true", help="Phase-2 weather flag")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    get_settings.cache_clear()

    print(f"Loading daily demand (lookback={args.lookback_days}d)...")
    daily = load_daily_demand(lookback_days=args.lookback_days)
    print(f"  rows={len(daily)}  skus={daily['item_id'].nunique() if not daily.empty else 0}")
    cats = load_item_categories()
    print(f"  categories loaded={len(cats)}  uplift_enabled={uplift_enabled()}")

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
    print(f"forecast rows={len(forecasts)}")
    print(f"wrote → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
