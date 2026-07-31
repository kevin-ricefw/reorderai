"""
Forecast store — nightly batch writes P50/P90; detect-order only reads.

Read order (Design Decision 5):
  1) data/forecast_store/latest_forecasts (batch ML)
  2) live ADS fallback from Wecomm sales
  3) stub (offline demos)
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import pandas as pd

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident
from v2.forecasting.forecast_store_io import (
    load_latest_classifications,
    load_latest_forecasts,
    load_latest_sku_uplift,
)
from v2.forecasting.pipeline import STANDARD_HORIZONS
from v2.forecasting.local_pos_sales import normalize_upc
from v2.forecasting.sku_uplift import sku_multiplier_for_date, sku_uplift_enabled

# ADS / demand_std window for ROP, safety stock, ADS cover (longer = more stable).
SALES_LOOKBACK_DAYS = int(os.getenv("ADS_LOOKBACK_DAYS", "90"))


def _lookup_keys(item_id: str, alt_ids: list[str] | None = None) -> list[str]:
    """Product id + UPC variants — forecast_store/uplift are often keyed by UPC."""
    keys: list[str] = []
    for raw in [item_id, *(alt_ids or [])]:
        if raw is None:
            continue
        s = str(raw).strip()
        if s and s not in keys:
            keys.append(s)
        n = normalize_upc(s)
        if n and n not in keys:
            keys.append(n)
    return keys


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
        self._sku_uplift: dict[str, dict[str, float]] | None = None
        self._ads_cache: dict[str, float] | None = None
        self._stats_cache: dict[str, dict[str, float]] | None = None
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
        self._sku_uplift = load_latest_sku_uplift()
        return self._batch if not self._batch.empty else None

    def _window_sku_uplift(
        self, keys: list[str], horizon_days: int
    ) -> tuple[float, str | None]:
        """Max learned SKU uplift across days in the upcoming order window."""
        if not sku_uplift_enabled():
            return 1.0, None
        table = self._sku_uplift if self._sku_uplift is not None else load_latest_sku_uplift()
        self._sku_uplift = table
        if not table:
            return 1.0, None
        best = 1.0
        best_name: str | None = None
        start = date.today()
        for key in keys:
            if key not in table:
                continue
            for i in range(max(int(horizon_days), 1)):
                m, n = sku_multiplier_for_date(key, table, as_of=start + timedelta(days=i))
                if m > best:
                    best, best_name = m, n
        return best, best_name

    def get_forecast(
        self,
        item_id: str,
        *,
        horizon_days: int,
        demand_class: str | None = None,
        alt_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        h = nearest_horizon(horizon_days)
        item = str(item_id)
        keys = _lookup_keys(item, alt_ids)

        batch = self._ensure_batch()
        if batch is not None:
            hit = batch[
                (batch["item_id"].astype(str).isin(keys))
                & (batch["horizon_days"].astype(int) == int(h))
            ]
            if not hit.empty:
                row = hit.iloc[0]
                self._mode = "batch"
                cls = demand_class or (
                    str(row["demand_class"]) if "demand_class" in row else None
                )
                p50 = float(row["p50"])
                p90 = float(row["p90"])
                uplift_m, uplift_rule = self._window_sku_uplift(keys, horizon_days)
                if uplift_m > 1.0:
                    p50 = round(p50 * uplift_m, 4)
                    p90 = round(p90 * uplift_m, 4)
                stats = self.get_demand_stats([item]).get(item) or {}
                ads = float(stats.get("ads") or 0.0)
                if ads <= 0 and h > 0:
                    # derive ADS from base (pre-uplift) P50 when stats missing
                    base_p50 = float(row["p50"])
                    ads = base_p50 / float(h)
                return {
                    "item_id": item,
                    "horizon_days": int(h),
                    "p50": p50,
                    "p90": p90,
                    "demand_class": cls,
                    "source": "forecast_store_batch",
                    "model": str(row.get("model", "")),
                    "uplift_multiplier": uplift_m,
                    "uplift_rule": uplift_rule,
                    "ads": ads,
                    "demand_std": float(stats.get("demand_std") or 0.0),
                }

        if self.use_live_ads and self.configured:
            self._mode = "live"
            out = self._live_sales_get(item, h, demand_class=demand_class)
            uplift_m, uplift_rule = self._window_sku_uplift(keys, horizon_days)
            if uplift_m > 1.0:
                out["p50"] = round(float(out["p50"]) * uplift_m, 4)
                out["p90"] = round(float(out["p90"]) * uplift_m, 4)
            out["uplift_multiplier"] = uplift_m
            out["uplift_rule"] = uplift_rule
            return out

        self._mode = "stub"
        return self._stub_get(item, h, demand_class=demand_class)

    def demand_class_for(self, item_id: str, alt_ids: list[str] | None = None) -> str | None:
        self._ensure_batch()
        if self._classes is None or self._classes.empty:
            return None
        keys = _lookup_keys(item_id, alt_ids)
        hit = self._classes[self._classes["item_id"].astype(str).isin(keys)]
        if hit.empty:
            return None
        return str(hit.iloc[0]["demand_class"])

    def get_demand_stats(self, item_ids: list[str] | None = None) -> dict[str, dict[str, float]]:
        """ADS + daily demand std from ai_pos_daily_sales (fallback: order_items ADS)."""
        if self._stats_cache is not None:
            if not item_ids:
                return self._stats_cache
            return {i: self._stats_cache.get(str(i), {"ads": 0.0, "demand_std": 0.0}) for i in item_ids}

        stats: dict[str, dict[str, float]] = {}
        if self.configured:
            try:
                sch = q_ident(self.schema)
                df = self._conn().read_sql(
                    f"""
                    SELECT
                      product_id::text AS item_id,
                      sale_date,
                      SUM(quantity) AS quantity
                    FROM {sch}.ai_pos_daily_sales
                    WHERE sale_date >= (CURRENT_DATE - INTERVAL '{int(SALES_LOOKBACK_DAYS)} days')
                    GROUP BY product_id, sale_date
                    """
                )
                if not df.empty:
                    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
                    for item, g in df.groupby(df["item_id"].astype(str)):
                        total = float(g["quantity"].sum())
                        ads = total / float(SALES_LOOKBACK_DAYS)
                        std = float(g["quantity"].std(ddof=1)) if len(g) > 1 else ads * 0.3
                        if pd.isna(std):
                            std = ads * 0.3
                        stats[str(item)] = {
                            "ads": max(ads, 0.0),
                            "demand_std": max(float(std), 0.0),
                        }
            except Exception:
                stats = {}

        if not stats:
            # Fallback: ADS only from live order_items
            ads_map = self._load_ads_map()
            stats = {
                k: {"ads": v, "demand_std": max(v * 0.3, 0.0)} for k, v in ads_map.items()
            }

        self._stats_cache = stats
        if not item_ids:
            return stats
        return {str(i): stats.get(str(i), {"ads": 0.0, "demand_std": 0.0}) for i in item_ids}

    def _load_ads_map(self) -> dict[str, float]:
        if self._ads_cache is not None:
            return self._ads_cache
        # Prefer stats cache
        if self._stats_cache is not None:
            self._ads_cache = {k: float(v.get("ads") or 0.0) for k, v in self._stats_cache.items()}
            return self._ads_cache
        sch = q_ident(self.schema)
        try:
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
        except Exception:
            self._ads_cache = {}
        return self._ads_cache

    def _live_sales_get(
        self,
        item_id: str,
        horizon: int,
        *,
        demand_class: str | None,
    ) -> dict[str, Any]:
        st = self.get_demand_stats([str(item_id)]).get(str(item_id)) or {}
        ads = float(st.get("ads") or 0.0)
        return {
            "item_id": item_id,
            "horizon_days": horizon,
            "p50": round(ads * horizon, 4),
            "p90": round(ads * 1.65 * horizon, 4),
            "demand_class": demand_class or ("smooth" if ads > 0 else "no_history"),
            "source": "sales_ads_fallback",
            "ads": ads,
            "demand_std": float(st.get("demand_std") or 0.0),
            "uplift_multiplier": 1.0,
            "uplift_rule": None,
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
