"""
Forecast store — nightly batch writes P50/P90; detect-order only reads.

Read order (Design Decision 5):
  1) data/forecast_store/latest_forecasts (batch ML)
  2) live ADS fallback from Wecomm sales
  3) stub (offline demos)
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident
from v2.forecasting.forecast_store_io import load_latest_classifications, load_latest_forecasts
from v2.forecasting.pipeline import STANDARD_HORIZONS

SALES_LOOKBACK_DAYS = 30


def nearest_horizon(x_days: int) -> int:
    x = max(int(x_days), 1)
    for h in STANDARD_HORIZONS:
        if h >= x:
            return h
    return STANDARD_HORIZONS[-1]


def _truthy(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).lower() in {"1", "true", "yes"}


class ForecastStore:
    def __init__(self) -> None:
        self.configured = bool(os.getenv("DB_HOST"))
        self.use_batch = _truthy("FORECAST_STORE_USE_BATCH", "1")
        self.use_live_ads = _truthy("FORECAST_STORE_USE_LIVE_SQL", "1")
        self.schema = get_tenant_schema()
        self._db: WecommDatabaseConnector | None = None
        self._batch: pd.DataFrame | None = None
        self._classes: pd.DataFrame | None = None
        self._ads_cache: dict[str, float] | None = None
        self._mode = "stub"

    @property
    def mode(self) -> str:
        return self._mode

    def _conn(self) -> WecommDatabaseConnector:
        if self._db is None:
            self._db = WecommDatabaseConnector()
        return self._db

    def _ensure_batch(self) -> pd.DataFrame | None:
        if self._batch is not None:
            return self._batch if not self._batch.empty else None
        if not self.use_batch:
            return None
        df = load_latest_forecasts()
        self._batch = df if df is not None else pd.DataFrame()
        self._classes = load_latest_classifications()
        return self._batch if not self._batch.empty else None

    def get_forecast(
        self,
        item_id: str,
        *,
        horizon_days: int,
        demand_class: str | None = None,
    ) -> dict[str, Any]:
        h = nearest_horizon(horizon_days)
        item = str(item_id)

        batch = self._ensure_batch()
        if batch is not None:
            hit = batch[
                (batch["item_id"].astype(str) == item)
                & (batch["horizon_days"].astype(int) == int(h))
            ]
            if not hit.empty:
                row = hit.iloc[0]
                self._mode = "batch"
                cls = demand_class or (
                    str(row["demand_class"]) if "demand_class" in row else None
                )
                return {
                    "item_id": item,
                    "horizon_days": int(h),
                    "p50": float(row["p50"]),
                    "p90": float(row["p90"]),
                    "demand_class": cls,
                    "source": "forecast_store_batch",
                    "model": str(row.get("model", "")),
                }

        if self.use_live_ads and self.configured:
            self._mode = "live"
            return self._live_sales_get(item, h, demand_class=demand_class)

        self._mode = "stub"
        return self._stub_get(item, h, demand_class=demand_class)

    def demand_class_for(self, item_id: str) -> str | None:
        self._ensure_batch()
        if self._classes is None or self._classes.empty:
            return None
        hit = self._classes[self._classes["item_id"].astype(str) == str(item_id)]
        if hit.empty:
            return None
        return str(hit.iloc[0]["demand_class"])

    def _load_ads_map(self) -> dict[str, float]:
        if self._ads_cache is not None:
            return self._ads_cache
        sch = q_ident(self.schema)
        df = self._conn().read_sql(
            f"""
            SELECT
              oi.product_id,
              COALESCE(
                SUM(
                  GREATEST(
                    COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0),
                    0
                  )
                ),
                0
              ) AS units
            FROM {sch}.order_items oi
            JOIN {sch}.orders o ON o.id = oi.order_id
            WHERE o.deleted_at IS NULL
              AND oi.deleted_at IS NULL
              AND COALESCE(o.is_return, FALSE) = FALSE
              AND o.created_at >= (NOW() - INTERVAL '{int(SALES_LOOKBACK_DAYS)} days')
            GROUP BY oi.product_id
            """
        )
        self._ads_cache = {
            str(int(r.product_id)): float(r.units) / float(SALES_LOOKBACK_DAYS)
            for r in df.itertuples(index=False)
        }
        return self._ads_cache

    def _live_sales_get(
        self,
        item_id: str,
        horizon: int,
        *,
        demand_class: str | None,
    ) -> dict[str, Any]:
        ads = float(self._load_ads_map().get(str(item_id), 0.0))
        return {
            "item_id": item_id,
            "horizon_days": horizon,
            "p50": round(ads * horizon, 4),
            "p90": round(ads * 1.65 * horizon, 4),
            "demand_class": demand_class or ("smooth" if ads > 0 else "no_history"),
            "source": "sales_ads_fallback",
            "ads": ads,
        }

    def _stub_get(
        self,
        item_id: str,
        horizon: int,
        *,
        demand_class: str | None,
    ) -> dict[str, Any]:
        seed = sum(ord(c) for c in str(item_id)) % 17
        daily_p50 = 1.5 + (seed % 5) * 0.7
        cls = (demand_class or "intermittent").lower()
        if cls in {"intermittent", "lumpy", "erratic"}:
            daily_p90 = daily_p50 * 1.9
        elif cls == "smooth":
            daily_p90 = daily_p50 * 1.25
        else:
            daily_p90 = daily_p50 * 1.5
        return {
            "item_id": item_id,
            "horizon_days": horizon,
            "p50": round(daily_p50 * horizon, 2),
            "p90": round(daily_p90 * horizon, 2),
            "demand_class": demand_class,
            "source": "forecast_store_stub",
        }
