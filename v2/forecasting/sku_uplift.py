"""
Per-SKU uplift learned from sales history + India/US calendar tags.

Only SKUs that historically sell more on weekends / festival windows get a
multiplier > 1. Flat SKUs stay at 1.0 (no blanket uplift).
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from v2.forecasting.festival_calendar import calendar_labels, is_weekend

# Min mean(special) / mean(baseline) to award uplift
_MIN_RATIO = 1.08
# Cap so one wild Diwali day cannot explode orders
_MAX_MULT = 1.75
# Need enough days in each bucket
_MIN_BASE_DAYS = 8
_MIN_SPECIAL_DAYS = 4


def sku_uplift_enabled() -> bool:
    return os.getenv("SKU_UPLIFT_ENABLED", "1").lower() in {"1", "true", "yes"}


def _as_date(as_of: str | date | datetime | None) -> date:
    if as_of is None:
        return date.today()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    return datetime.fromisoformat(str(as_of).replace("Z", "")).date()


def _ratio_to_mult(special_mean: float, base_mean: float) -> float:
    if base_mean <= 1e-9:
        return 1.0
    ratio = float(special_mean) / float(base_mean)
    if ratio < _MIN_RATIO:
        return 1.0
    return float(min(max(ratio, 1.0), _MAX_MULT))


def learn_sku_uplift_table(daily: pd.DataFrame) -> dict[str, dict[str, float]]:
    """
    Learn per-item multipliers keyed by label: ``weekend`` and festival names.

    Returns: { item_id: { label: multiplier, ... }, ... }
    Only labels with multiplier > 1.0 are stored.
    """
    if daily is None or daily.empty:
        return {}

    df = daily.copy()
    df["item_id"] = df["item_id"].astype(str)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
    df["d"] = df["date"].dt.date

    # Tag every calendar day present (including zero-sale days after reindex? —
    # we only have observed rows; for sparse items zeros matter less for uplift)
    unique_days = sorted(set(df["d"].tolist()))
    day_tags = {d: calendar_labels(d) for d in unique_days}

    # Baseline days: Tue–Thu, not festival, not weekend
    baseline_days = {
        d
        for d in unique_days
        if (not is_weekend(d))
        and d.weekday() in {1, 2, 3}
        and not any(t != "weekend" for t in day_tags.get(d, []))
    }

    table: dict[str, dict[str, float]] = {}

    for item_id, g in df.groupby("item_id"):
        by_day = g.groupby("d", as_index=False)["quantity"].sum()
        day_map = {r.d: float(r.quantity) for r in by_day.itertuples(index=False)}

        base_vals = [day_map[d] for d in baseline_days if d in day_map]
        # If sparse item never sold on baseline Tue–Thu, use all non-weekend non-festival
        if len(base_vals) < _MIN_BASE_DAYS:
            alt = [
                day_map[d]
                for d in unique_days
                if d in day_map
                and not is_weekend(d)
                and not any(t != "weekend" for t in day_tags.get(d, []))
            ]
            base_vals = alt
        if len(base_vals) < _MIN_BASE_DAYS:
            continue
        base_mean = float(np.mean(base_vals))
        if base_mean <= 0:
            continue

        lifts: dict[str, float] = {}

        # Weekend
        weekend_days = [d for d in unique_days if is_weekend(d) and d in day_map]
        if len(weekend_days) >= _MIN_SPECIAL_DAYS:
            w_mean = float(np.mean([day_map[d] for d in weekend_days]))
            m = _ratio_to_mult(w_mean, base_mean)
            if m > 1.0:
                lifts["weekend"] = round(m, 4)

        # Each festival tag
        tag_to_days: dict[str, list[date]] = {}
        for d in unique_days:
            if d not in day_map:
                continue
            for tag in day_tags.get(d, []):
                if tag == "weekend":
                    continue
                tag_to_days.setdefault(tag, []).append(d)

        for tag, days in tag_to_days.items():
            if len(days) < max(2, _MIN_SPECIAL_DAYS // 2):
                continue
            t_mean = float(np.mean([day_map[d] for d in days]))
            m = _ratio_to_mult(t_mean, base_mean)
            if m > 1.0:
                lifts[tag] = round(m, 4)

        if lifts:
            table[str(item_id)] = lifts

    return table


def _label_allowed(lab: str, allowed: set[str] | None) -> bool:
    """Filter calendar labels by request uplift_types (weekend / festival / trend)."""
    if allowed is None:
        return True
    if not allowed:
        return False
    if lab == "weekend":
        return "weekend" in allowed
    # Named festival tags (Diwali, etc.) — not "weekend"
    if "festival" in allowed:
        return True
    # "trend" reserved — no calendar labels yet
    return False


def sku_multiplier_for_date(
    item_id: str,
    table: dict[str, dict[str, float]],
    *,
    as_of: str | date | datetime | None = None,
    allowed_types: set[str] | list[str] | None = None,
) -> tuple[float, str | None]:
    """
    Best (max) applicable learned multiplier for as_of labels.
    Returns (1.0, None) if no selective uplift.

    ``allowed_types``: subset of {weekend, festival, trend}.
    ``None`` = all types (legacy). Empty set/list = force no uplift.
    """
    if not sku_uplift_enabled() or not table:
        return 1.0, None
    allowed: set[str] | None
    if allowed_types is None:
        allowed = None
    else:
        allowed = {str(x).strip().lower() for x in allowed_types if str(x).strip()}
        if not allowed:
            return 1.0, None

    lifts = table.get(str(item_id))
    if not lifts:
        return 1.0, None

    d = _as_date(as_of)
    labels = calendar_labels(d)
    best = 1.0
    best_name: str | None = None
    for lab in labels:
        if not _label_allowed(str(lab), allowed):
            continue
        m = float(lifts.get(lab, 1.0))
        if m > best:
            best = m
            best_name = f"sku_{lab}"
    return best, best_name


def summarize_sku_uplift(table: dict[str, dict[str, float]]) -> dict[str, Any]:
    weekend_n = sum(1 for v in table.values() if "weekend" in v)
    fest_n = sum(1 for v in table.values() if any(k != "weekend" for k in v))
    return {
        "skus_with_any_lift": len(table),
        "skus_with_weekend_lift": weekend_n,
        "skus_with_festival_lift": fest_n,
    }
