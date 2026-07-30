"""Safety stock calculations."""

from __future__ import annotations

import numpy as np


def calculate_safety_stock(
    average_daily_demand: float,
    demand_std: float,
    lead_time_days: float,
    service_level: float = 0.95,
) -> float:
    """
    Calculate safety stock using lead-time demand variability.

    SS = Z × σ_demand × √lead_time
    """
    if average_daily_demand <= 0:
        return 0.0

    z_scores = {0.90: 1.28, 0.95: 1.65, 0.99: 2.33}
    z = z_scores.get(service_level, 1.65)
    std = demand_std if demand_std > 0 else average_daily_demand * 0.3
    lt = max(lead_time_days, 1)
    return round(z * std * np.sqrt(lt), 2)
