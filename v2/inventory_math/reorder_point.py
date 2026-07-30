"""Dynamic reorder point calculations."""

from __future__ import annotations


def calculate_dynamic_reorder_point(
    average_daily_demand: float,
    lead_time_days: float,
    safety_stock: float,
) -> float:
    """
    Dynamic Reorder Point = Lead Time Demand + Safety Stock.

    Lead Time Demand = average_daily_demand × lead_time_days
    """
    lt = max(lead_time_days, 1)
    lead_time_demand = average_daily_demand * lt
    return round(lead_time_demand + safety_stock, 2)
