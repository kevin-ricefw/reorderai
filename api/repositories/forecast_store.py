"""
Forecast store — nightly batch writes P50/P90; detect-order API only reads.

Standard horizons: 7 / 14 / 21 / 30 / 45 days (Decision 5).
"""

from __future__ import annotations

import os
from typing import Any

STANDARD_HORIZONS = (7, 14, 21, 30, 45)


def nearest_horizon(x_days: int) -> int:
    """Pick the smallest standard horizon >= X; else the largest available."""
    x = max(int(x_days), 1)
    for h in STANDARD_HORIZONS:
        if h >= x:
            return h
    return STANDARD_HORIZONS[-1]


class ForecastStore:
    """
    Read P50/P90 demand totals for an item over a planning horizon.

    Live mode: query forecast_store table once NEW_ORDER_DB / forecast DB is wired.
    Stub mode: deterministic pseudo-forecasts so the API is testable now.
    """

    def __init__(self) -> None:
        self.configured = bool(os.getenv("DB_HOST") or os.getenv("FORECAST_STORE_URL"))
        self.live = os.getenv("FORECAST_STORE_USE_LIVE_SQL", "").lower() in {
            "1",
            "true",
            "yes",
        }

    @property
    def mode(self) -> str:
        return "live" if self.live else "stub"

    def get_forecast(
        self,
        item_id: str,
        *,
        horizon_days: int,
        demand_class: str | None = None,
    ) -> dict[str, Any]:
        h = nearest_horizon(horizon_days)
        if self.live:
            return self._live_get(item_id, h)
        return self._stub_get(item_id, h, demand_class=demand_class)

    def _stub_get(
        self,
        item_id: str,
        horizon: int,
        *,
        demand_class: str | None,
    ) -> dict[str, Any]:
        # Stable pseudo values keyed by item_id so demos are repeatable
        seed = sum(ord(c) for c in str(item_id)) % 17
        daily_p50 = 1.5 + (seed % 5) * 0.7
        # Intermittent / lumpy → wider P90 gap
        cls = (demand_class or "intermittent").lower()
        if cls in {"intermittent", "lumpy", "erratic"}:
            daily_p90 = daily_p50 * (1.8 + (seed % 3) * 0.15)
        elif cls == "smooth":
            daily_p90 = daily_p50 * 1.25
        else:
            daily_p90 = daily_p50 * 1.5

        p50 = round(daily_p50 * horizon, 2)
        p90 = round(daily_p90 * horizon, 2)
        return {
            "item_id": item_id,
            "horizon_days": horizon,
            "p50": p50,
            "p90": p90,
            "demand_class": demand_class,
            "source": "forecast_store_stub",
        }

    def _live_get(self, item_id: str, horizon: int) -> dict[str, Any]:
        # TODO: SELECT p50, p90 FROM forecast_store
        #       WHERE item_id=:id AND horizon_days=:h ORDER BY as_of DESC LIMIT 1
        raise NotImplementedError("Live forecast_store SQL not wired yet.")
