"""
Croston-SBA (Intermittent) and TSB (Erratic/Lumpy) — Decision 1.

Outputs expected demand per day; P50/P90 come from Monte Carlo using these params.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class IntermittentParams:
    model: str  # sba | tsb | rule
    demand_size: float  # expected non-zero demand size
    demand_probability: float  # probability of a demand day
    expected_daily: float


def fit_croston_sba(daily: pd.Series, alpha: float = 0.1) -> IntermittentParams:
    """Syntetos–Boylan approximation of Croston."""
    y = pd.to_numeric(daily, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    nz = y[y > 0]
    if len(nz) == 0:
        return IntermittentParams("sba", 0.0, 0.0, 0.0)
    if len(nz) == 1:
        p = 1.0 / max(len(y), 1)
        return IntermittentParams("sba", float(nz[0]), p, float(nz[0]) * p)

    z = float(nz[0])
    # intervals between non-zero observations
    idx = np.flatnonzero(y > 0)
    gaps = np.diff(idx)
    x = float(gaps[0]) if len(gaps) else float(len(y))

    for i in range(1, len(idx)):
        demand = float(y[idx[i]])
        interval = float(idx[i] - idx[i - 1])
        z = z + alpha * (demand - z)
        x = x + alpha * (interval - x)

    x = max(x, 1.0)
    # SBA bias correction: (1 - alpha/2) * z / x
    daily_exp = (1.0 - alpha / 2.0) * z / x
    p = 1.0 / x
    return IntermittentParams("sba", z, p, float(max(daily_exp, 0.0)))


def fit_tsb(daily: pd.Series, alpha: float = 0.1, beta: float = 0.1) -> IntermittentParams:
    """Teunter–Syntetos–Babai — updates probability every period (handles long zeros)."""
    y = pd.to_numeric(daily, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if len(y) == 0:
        return IntermittentParams("tsb", 0.0, 0.0, 0.0)

    nz = y[y > 0]
    if len(nz) == 0:
        return IntermittentParams("tsb", 0.0, 0.0, 0.0)

    z = float(nz[0])
    p = float(len(nz) / len(y))

    for val in y:
        if val > 0:
            z = z + alpha * (float(val) - z)
            p = p + beta * (1.0 - p)
        else:
            p = p + beta * (0.0 - p)

    p = float(np.clip(p, 0.0, 1.0))
    return IntermittentParams("tsb", z, p, float(max(z * p, 0.0)))


def fit_rule_based(daily: pd.Series) -> IntermittentParams:
    """Single-demand-day / cold start — no statistical model."""
    y = pd.to_numeric(daily, errors="coerce").fillna(0.0)
    total = float(y.sum())
    n = max(int(len(y)), 1)
    size = float(y[y > 0].iloc[0]) if (y > 0).any() else 0.0
    # Spread known volume thinly; API can still order conservatively via P90 MC
    daily_exp = total / n
    p = 1.0 / n if total > 0 else 0.0
    return IntermittentParams("rule", size, p, float(daily_exp))
