"""
Phase-1/2 nightly forecast pipeline (Design §5.2).

Classify → route model → P50/P90 → optional category uplift → forecast_store.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from v2.forecasting.croston import fit_croston_sba, fit_rule_based, fit_tsb
from v2.forecasting.monte_carlo import simulate_horizon_demand
from v2.forecasting.smooth_lgbm import fit_pooled_lightgbm, forecast_smooth_horizons
from v2.forecasting.syntetos_boylan import classify_demand_series, classify_sku_frame
from v2.forecasting.uplift import apply_uplift_to_forecasts

STANDARD_HORIZONS = (7, 14, 21, 30, 45)


def _series_for_item(daily: pd.DataFrame, item_id: str) -> pd.Series:
    g = daily.loc[daily["item_id"].astype(str) == str(item_id)].sort_values("date")
    if g.empty:
        return pd.Series(dtype=float)
    s = g.set_index("date")["quantity"]
    full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full_idx, fill_value=0.0)


def forecast_item(
    daily_series: pd.Series,
    *,
    demand_class: str,
    horizons: Iterable[int] = STANDARD_HORIZONS,
    item_id: str = "",
    smooth_model=None,
) -> list[dict]:
    """Route by demand class (Decision 1) and produce P50/P90 per horizon."""
    cls = (demand_class or "intermittent").lower()
    horizon_list = [int(h) for h in horizons]

    if cls == "smooth":
        return forecast_smooth_horizons(
            daily_series,
            horizons=horizon_list,
            item_id=item_id,
            pooled_model=smooth_model,
        )

    if cls == "intermittent":
        params = fit_croston_sba(daily_series)
        model = "croston_sba"
    elif cls in {"erratic", "lumpy"}:
        params = fit_tsb(daily_series)
        model = "tsb"
    else:
        params = fit_rule_based(daily_series)
        model = "rule"

    seed_base = abs(hash(str(item_id))) % (2**31)
    rows: list[dict] = []
    for h in horizon_list:
        p50, p90 = simulate_horizon_demand(
            daily_series,
            params,
            horizon_days=h,
            seed=seed_base + int(h),
        )
        rows.append(
            {
                "item_id": str(item_id),
                "horizon_days": int(h),
                "p50": p50,
                "p90": p90,
                "demand_class": cls,
                "model": model,
            }
        )
    return rows


def build_forecast_store_frame(
    daily: pd.DataFrame,
    *,
    horizons: Iterable[int] = STANDARD_HORIZONS,
    item_category: dict[str, str] | None = None,
    as_of: str | None = None,
    weather_hot: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    classifications = classify_sku_frame(daily)
    if classifications.empty:
        return classifications, pd.DataFrame(
            columns=[
                "item_id",
                "horizon_days",
                "p50",
                "p90",
                "demand_class",
                "model",
                "uplift_multiplier",
                "uplift_rule",
            ]
        )

    smooth_ids = (
        classifications.loc[classifications["demand_class"] == "smooth", "item_id"]
        .astype(str)
        .tolist()
    )
    smooth_model = fit_pooled_lightgbm(daily, smooth_ids)

    forecast_rows: list[dict] = []
    for row in classifications.itertuples(index=False):
        series = _series_for_item(daily, str(row.item_id))
        forecast_rows.extend(
            forecast_item(
                series,
                demand_class=str(row.demand_class),
                horizons=horizons,
                item_id=str(row.item_id),
                smooth_model=smooth_model if str(row.demand_class) == "smooth" else None,
            )
        )

    forecasts = pd.DataFrame(forecast_rows)
    forecasts = apply_uplift_to_forecasts(
        forecasts,
        item_category=item_category,
        as_of=as_of,
        weather_hot=weather_hot,
        daily=daily,
    )
    return classifications, forecasts


def run_forecast_pipeline(
    daily: pd.DataFrame,
    *,
    horizons: Iterable[int] = STANDARD_HORIZONS,
    item_category: dict[str, str] | None = None,
    weather_hot: bool = False,
) -> dict[str, pd.DataFrame | str]:
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    classifications, forecasts = build_forecast_store_frame(
        daily,
        horizons=horizons,
        item_category=item_category,
        as_of=as_of,
        weather_hot=weather_hot,
    )
    return {
        "as_of": as_of,
        "classifications": classifications,
        "forecasts": forecasts,
    }


__all__ = [
    "STANDARD_HORIZONS",
    "build_forecast_store_frame",
    "forecast_item",
    "run_forecast_pipeline",
    "classify_demand_series",
]
