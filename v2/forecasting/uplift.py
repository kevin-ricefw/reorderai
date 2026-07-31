"""
Demand uplift applied after base P50/P90.

1) Per-SKU learned weekend/festival multipliers (SKU_UPLIFT_ENABLED, default on)
2) Optional coarse category rules (UPLIFT_ENABLED, default off)

SKU uplift is selective: only items that historically spike get m > 1.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import pandas as pd

from v2.forecasting.sku_uplift import (
    learn_sku_uplift_table,
    sku_multiplier_for_date,
    sku_uplift_enabled,
    summarize_sku_uplift,
)

# category_key (lower) → list of rules (legacy / optional)
DEFAULT_FESTIVAL_RULES: list[dict[str, Any]] = [
    {
        "name": "diwali_week",
        "months": [10, 11],
        "day_range": (1, 20),
        "categories": ["*"],
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
    """Category-rule uplift (coarse). Off by default."""
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
    """Return (multiplier, rule_name). 1.0 when disabled / no match."""
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
    daily: pd.DataFrame | None = None,
    sku_uplift_table: dict[str, dict[str, float]] | None = None,
) -> pd.DataFrame:
    """
    Multiply p50/p90 by best applicable uplift.

    Priority: max(sku_learned, category_rule) — never blanket-apply to all SKUs
    via SKU path; category rules only if UPLIFT_ENABLED=1.
    """
    if forecasts.empty:
        return forecasts

    out = forecasts.copy()
    cats = item_category or {}
    table = sku_uplift_table
    if table is None and daily is not None and sku_uplift_enabled():
        table = learn_sku_uplift_table(daily)
    table = table or {}

    multis: list[float] = []
    names: list[str | None] = []
    for item_id in out["item_id"].astype(str):
        sku_m, sku_n = sku_multiplier_for_date(item_id, table, as_of=as_of)
        cat_m, cat_n = category_multiplier(
            cats.get(item_id), as_of=as_of, weather_hot=weather_hot
        )
        if sku_m >= cat_m and sku_m > 1.0:
            multis.append(sku_m)
            names.append(sku_n)
        elif cat_m > 1.0:
            multis.append(cat_m)
            names.append(cat_n)
        else:
            multis.append(1.0)
            names.append(None)

    out["uplift_multiplier"] = multis
    out["uplift_rule"] = names
    out["p50"] = (out["p50"].astype(float) * out["uplift_multiplier"]).round(4)
    out["p90"] = (out["p90"].astype(float) * out["uplift_multiplier"]).round(4)
    return out


__all__ = [
    "DEFAULT_FESTIVAL_RULES",
    "apply_uplift_to_forecasts",
    "category_multiplier",
    "learn_sku_uplift_table",
    "sku_uplift_enabled",
    "summarize_sku_uplift",
    "uplift_enabled",
]
