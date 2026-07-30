"""
Syntetos–Boylan demand classification (Decision 1).

ADI threshold 1.32, CV² threshold 0.49 → Smooth / Intermittent / Erratic / Lumpy.
Plus "single_demand_day" when history is too thin.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

ADI_THRESHOLD = 1.32
CV2_THRESHOLD = 0.49


def _adi(nonzero_day_indices: np.ndarray) -> float:
    if len(nonzero_day_indices) < 2:
        return float("inf")
    gaps = np.diff(np.sort(nonzero_day_indices.astype(float)))
    gaps = gaps[gaps > 0]
    if len(gaps) == 0:
        return float("inf")
    return float(np.mean(gaps))


def _cv2(nonzero_sizes: np.ndarray) -> float:
    if len(nonzero_sizes) < 2:
        return 0.0
    mean = float(np.mean(nonzero_sizes))
    if mean <= 0:
        return 0.0
    return float((np.std(nonzero_sizes, ddof=1) / mean) ** 2)


def classify_demand_series(daily_demand: pd.Series) -> dict[str, Any]:
    """
    Classify one SKU from a daily demand series (index=date or int day, values=qty).

    Returns demand_class, adi, cv2, nonzero_days, total_units.
    """
    y = pd.to_numeric(daily_demand, errors="coerce").fillna(0.0).astype(float)
    nonzero_mask = y > 0
    nonzero = y[nonzero_mask]
    n_nz = int(nonzero_mask.sum())
    total_units = float(y.sum())

    if n_nz == 0:
        return {
            "demand_class": "single_demand_day",
            "adi": None,
            "cv2": None,
            "nonzero_days": 0,
            "total_units": 0.0,
        }
    if n_nz == 1:
        return {
            "demand_class": "single_demand_day",
            "adi": None,
            "cv2": 0.0,
            "nonzero_days": 1,
            "total_units": total_units,
        }

    # Use positional indices for ADI (average gap in days between sales)
    positions = np.flatnonzero(nonzero_mask.to_numpy())
    adi = _adi(positions)
    cv2 = _cv2(nonzero.to_numpy())

    if adi < ADI_THRESHOLD and cv2 < CV2_THRESHOLD:
        cls = "smooth"
    elif adi >= ADI_THRESHOLD and cv2 < CV2_THRESHOLD:
        cls = "intermittent"
    elif adi < ADI_THRESHOLD and cv2 >= CV2_THRESHOLD:
        cls = "erratic"
    else:
        cls = "lumpy"

    return {
        "demand_class": cls,
        "adi": round(adi, 4) if np.isfinite(adi) else None,
        "cv2": round(cv2, 4),
        "nonzero_days": n_nz,
        "total_units": total_units,
    }


def classify_sku_frame(daily: pd.DataFrame) -> pd.DataFrame:
    """
    daily columns: item_id, date, quantity
    Returns one row per item_id with classification fields.
    """
    if daily.empty:
        return pd.DataFrame(
            columns=["item_id", "demand_class", "adi", "cv2", "nonzero_days", "total_units"]
        )

    rows: list[dict[str, Any]] = []
    for item_id, g in daily.groupby("item_id", sort=False):
        g = g.sort_values("date")
        s = g.set_index("date")["quantity"]
        # Reindex to full calendar for correct ADI day gaps
        if len(s) > 0:
            full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
            s = s.reindex(full_idx, fill_value=0.0)
        meta = classify_demand_series(s)
        rows.append({"item_id": str(item_id), **meta})
    return pd.DataFrame(rows)
