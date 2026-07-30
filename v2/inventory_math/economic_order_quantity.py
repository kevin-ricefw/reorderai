"""Economic Order Quantity (EOQ)."""

from __future__ import annotations

import math


def calculate_eoq(
    annual_demand: float,
    ordering_cost: float = 50.0,
    holding_cost_per_unit: float = 2.0,
) -> float:
    """
    EOQ = √((2 × D × S) / H)

    Args:
        annual_demand: Units per year.
        ordering_cost: Cost per order (S).
        holding_cost_per_unit: Holding cost per unit per year (H).
    """
    if annual_demand <= 0 or holding_cost_per_unit <= 0:
        return 0.0
    return round(math.sqrt((2 * annual_demand * ordering_cost) / holding_cost_per_unit), 2)
