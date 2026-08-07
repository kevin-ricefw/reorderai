"""Safety stock calculations."""

from __future__ import annotations

import math
from statistics import NormalDist


def calculate_safety_stock(
    average_daily_demand: float,
    demand_std: float,
    lead_time_days: float,
    service_level: float = 0.95,
) -> float:
    """
    Calculate safety stock using lead-time demand variability.

    SS = Z × σ_demand × √lead_time, where Z is the inverse-normal-CDF value
    for the requested service_level (e.g. 0.95 → Z≈1.65, 0.75 → Z≈0.67).
    """
    if average_daily_demand <= 0:
        return 0.0

    # P50 (service_level<=0.5) needs no buffer; cap below 1.0 since P100 is
    # a mathematically infinite Z-score under a normal model.
    p = min(max(float(service_level), 0.5), 0.999)
    z = NormalDist().inv_cdf(p) if p > 0.5 else 0.0
    std = demand_std if demand_std > 0 else average_daily_demand * 0.3
    lt = max(lead_time_days, 1)
    return round(z * std * math.sqrt(lt), 2)
