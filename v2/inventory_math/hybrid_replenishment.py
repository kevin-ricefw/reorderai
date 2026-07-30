"""
Replenishment math on Prophet + LightGBM hybrid demand.

NO expiry / waste haircut. Physical on-hand is used as-is.

  Safety Stock  = Sum(Prophet yhat_upper) − Sum(Prophet yhat)   over (L + C)
  ROP           = Sum(Hybrid demand over Lead Time L) + Safety Stock
  Order Qty     = max(0, Hybrid demand over (L+C) + Safety Stock − Physical stock)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from v2.forecasting.hybrid_prophet_lgbm import (
    HybridForecastResult,
    fit_hybrid_residual_engine,
)


@dataclass
class ReplenishmentDecision:
    lead_time_days: int
    days_to_cover: int
    window_days: int
    hybrid_demand_L: float
    hybrid_demand_L_plus_C: float
    prophet_yhat_sum_window: float
    prophet_upper_sum_window: float
    safety_stock: float
    reorder_point: float
    physical_stock: float
    recommended_order_qty: float
    model_metrics: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prophet_structural_safety_stock(future: pd.DataFrame) -> float:
    """Sum(yhat_upper) − Sum(yhat) across the planning window."""
    if future.empty:
        return 0.0
    upper = float(future["yhat_upper"].sum())
    base = float(future["yhat"].sum())
    return max(upper - base, 0.0)


def hybrid_demand_first_n_days(future: pd.DataFrame, n: int) -> float:
    if future.empty or n <= 0:
        return 0.0
    return float(future.head(int(n))["hybrid_yhat"].sum())


def compute_replenishment(
    hybrid: HybridForecastResult,
    *,
    lead_time_days: int,
    days_to_cover: int,
    physical_stock: float,
) -> ReplenishmentDecision:
    """Inventory optimization core using hybrid future frame sized to L+C."""
    L = max(int(lead_time_days), 1)
    C = max(int(days_to_cover), 0)
    window = L + C
    future = hybrid.future.copy()
    if len(future) < window:
        raise ValueError(
            f"Hybrid future has {len(future)} days but L+C={window} required."
        )
    window_frame = future.head(window)

    safety = prophet_structural_safety_stock(window_frame)
    demand_L = hybrid_demand_first_n_days(window_frame, L)
    demand_LC = float(window_frame["hybrid_yhat"].sum())
    rop = demand_L + safety
    physical = float(physical_stock)
    order_qty = max(0.0, demand_LC + safety - physical)

    return ReplenishmentDecision(
        lead_time_days=L,
        days_to_cover=C,
        window_days=window,
        hybrid_demand_L=round(demand_L, 2),
        hybrid_demand_L_plus_C=round(demand_LC, 2),
        prophet_yhat_sum_window=round(float(window_frame["yhat"].sum()), 2),
        prophet_upper_sum_window=round(float(window_frame["yhat_upper"].sum()), 2),
        safety_stock=round(safety, 2),
        reorder_point=round(rop, 2),
        physical_stock=round(physical, 2),
        recommended_order_qty=round(order_qty, 2),
        model_metrics=dict(hybrid.metrics),
    )


def run_hybrid_replenishment_engine(
    sales: pd.DataFrame,
    order_log: pd.DataFrame,
    *,
    lead_time_days: int,
    days_to_cover: int,
    physical_stock: float,
) -> tuple[HybridForecastResult, ReplenishmentDecision]:
    """
    Dashboard entry: L, C, stock + sales + manual order pattern → decision.

    ``order_log`` feeds LightGBM residuals only (receive cadence). Not inventory.
    """
    hybrid = fit_hybrid_residual_engine(
        sales,
        order_log,
        lead_time_days=lead_time_days,
        days_to_cover=days_to_cover,
    )
    decision = compute_replenishment(
        hybrid,
        lead_time_days=lead_time_days,
        days_to_cover=days_to_cover,
        physical_stock=physical_stock,
    )
    return hybrid, decision
