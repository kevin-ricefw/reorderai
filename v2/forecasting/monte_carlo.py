"""
Monte Carlo P50/P90 for intermittent-style demand (Decision 2 + 3).

Simulate horizon demand as Bernoulli(p) * size draws from empirical non-zero sizes
(or lognormal around fitted size when history is thin).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from v2.forecasting.croston import IntermittentParams


def simulate_horizon_demand(
    daily: pd.Series,
    params: IntermittentParams,
    *,
    horizon_days: int,
    n_sims: int = 2000,
    seed: int | None = None,
) -> tuple[float, float]:
    """Return (p50, p90) total demand over horizon_days."""
    h = max(int(horizon_days), 1)
    rng = np.random.default_rng(seed)

    y = pd.to_numeric(daily, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    nz = y[y > 0]

    p = float(np.clip(params.demand_probability, 0.0, 1.0))
    if p <= 0 and params.expected_daily <= 0:
        return 0.0, 0.0

    if len(nz) >= 3:
        sizes = nz.astype(float)
        # Bootstrap non-zero sizes
        size_draws = rng.choice(sizes, size=(n_sims, h), replace=True)
    else:
        mu = max(params.demand_size, float(np.mean(nz)) if len(nz) else 0.0, 0.01)
        # Lognormal around size with mild variance
        sigma = 0.35
        size_draws = rng.lognormal(mean=np.log(mu), sigma=sigma, size=(n_sims, h))

    occurs = rng.random((n_sims, h)) < p
    totals = (size_draws * occurs).sum(axis=1)

    p50 = float(np.percentile(totals, 50))
    p90 = float(np.percentile(totals, 90))
    return round(max(p50, 0.0), 4), round(max(p90, 0.0), 4)


def smooth_percentile_forecast(
    daily: pd.Series,
    *,
    horizon_days: int,
) -> tuple[float, float]:
    """
    Smooth items: use recent daily distribution (empirical) for horizon totals.
    LightGBM can replace the mean later; percentiles stay simulation-based.
    """
    h = max(int(horizon_days), 1)
    y = pd.to_numeric(daily, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if len(y) == 0:
        return 0.0, 0.0

    rng = np.random.default_rng(abs(hash(y.tobytes())) % (2**32))
    # Bootstrap daily demand over horizon
    draws = rng.choice(y, size=(2000, h), replace=True)
    totals = draws.sum(axis=1)
    return (
        round(float(np.percentile(totals, 50)), 4),
        round(float(np.percentile(totals, 90)), 4),
    )
