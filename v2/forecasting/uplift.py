"""
Phase 2 — category-level weather / festival uplift (Decision 6).

Toggle with UPLIFT_ENABLED=1. Factors are config-driven so a wrong uplift
never ships silently; default table is conservative / off unless enabled.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pandas as pd

# category_key (lower) → list of rules
# multiplier applies to P50/P90 when rule matches as_of date
DEFAULT_FESTIVAL_RULES: list[dict[str, Any]] = [
    # Example validated-style rules (disabled unless UPLIFT_ENABLED)
    {
        "name": "diwali_week",
        "months": [10, 11],
        "day_range": (1, 20),
        "categories": ["*"],  # all categories when enabled
        "multiplier": 1.15,
    },
    {
        "name": "summer_heat_beverages",
        "months": [6, 7, 8],
        "day_range": (1, 31),
        "categories": ["beverage", "beverages", "drink", "drinks", "dairy"],
        "multiplier": 1.25,
    },
]


def uplift_enabled() -> bool:
    return os.getenv("UPLIFT_ENABLED", "0").lower() in {"1", "true", "yes"}


def _as_date(as_of: str | date | datetime | None) -> date:
    if as_of is None:
        return date.today()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return datetime.fromisoformat(str(as_of).replace("Z", "")).date()


def category_multiplier(
    category: str | None,
    *,
    as_of: str | date | datetime | None = None,
    weather_hot: bool = False,
) -> tuple[float, str | None]:
    """
    Return (multiplier, rule_name). 1.0 when disabled / no match.
    weather_hot can boost summer beverage rule further.
    """
    if not uplift_enabled():
        return 1.0, None

    d = _as_date(as_of)
    cat = (category or "").strip().lower()
    best = 1.0
    best_name: str | None = None

    for rule in DEFAULT_FESTIVAL_RULES:
        if d.month not in rule["months"]:
            continue
        lo, hi = rule["day_range"]
        if not (lo <= d.day <= hi):
            continue
        cats = [c.lower() for c in rule["categories"]]
        if "*" not in cats and cat not in cats and not any(c in cat for c in cats if c != "*"):
            continue
        m = float(rule["multiplier"])
        if weather_hot and "summer" in str(rule["name"]):
            m *= 1.1
        if m > best:
            best = m
            best_name = str(rule["name"])

    return best, best_name


def apply_uplift_to_forecasts(
    forecasts: pd.DataFrame,
    *,
    item_category: dict[str, str] | None = None,
    as_of: str | None = None,
    weather_hot: bool = False,
) -> pd.DataFrame:
    """Multiply p50/p90 by category uplift; add uplift_* columns."""
    if forecasts.empty:
        return forecasts

    out = forecasts.copy()
    cats = item_category or {}
    multis: list[float] = []
    names: list[str | None] = []
    for item_id in out["item_id"].astype(str):
        m, n = category_multiplier(cats.get(item_id), as_of=as_of, weather_hot=weather_hot)
        multis.append(m)
        names.append(n)
    out["uplift_multiplier"] = multis
    out["uplift_rule"] = names
    out["p50"] = (out["p50"].astype(float) * out["uplift_multiplier"]).round(4)
    out["p90"] = (out["p90"].astype(float) * out["uplift_multiplier"]).round(4)
    return out
