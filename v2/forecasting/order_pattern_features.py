"""
Manual invoice / receive history → order-pattern features (NO expiry).

The scraped invoice file is only a record of *when the store received stock*
(order cadence pattern). Expiry is ignored entirely.

Features aligned onto each calendar day ``ds``:
  - days_since_last_receipt : days since most recent Received Date <= ds
  - last_receipt_gap_days   : gap between the last two receipts as of ds
                              (how often they tend to reorder)
  - receipts_last_7d        : count of receipt events in (ds-6 .. ds)
  - qty_received_last_7d    : sum of received qty in that same 7-day window
                              (1.0 per event if quantity column missing)
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

# Before any receipt exists in history
_NO_RECEIPT_SENTINEL = 999.0


def _normalize_order_log(invoices: pd.DataFrame) -> pd.DataFrame:
    """Require Received Date; quantity optional; Expiry Date ignored if present."""
    df = invoices.copy()
    cols = {c.lower().strip().replace(" ", "_"): c for c in df.columns}

    def _pick(*aliases: str) -> str | None:
        for a in aliases:
            if a in cols:
                return cols[a]
        return None

    recv = _pick(
        "received_date",
        "received",
        "recv_date",
        "delivery_date",
        "invoice_date",
    )
    if recv is None:
        raise ValueError(
            "Order-pattern frame needs a Received Date (or Delivery / Invoice Date)."
        )
    qty = _pick("quantity", "units", "qty", "pallet_units", "cases", "ordered_qty")

    out = pd.DataFrame(
        {
            "received_date": pd.to_datetime(df[recv], errors="coerce").dt.normalize(),
        }
    )
    if qty is not None:
        out["quantity"] = pd.to_numeric(df[qty], errors="coerce").fillna(1.0)
    else:
        out["quantity"] = 1.0
    return out.dropna(subset=["received_date"]).sort_values("received_date")


def build_order_pattern_features(
    dates: Iterable[pd.Timestamp] | pd.Series | pd.DatetimeIndex,
    invoices: pd.DataFrame,
) -> pd.DataFrame:
    """
    Row-per-date order-pattern features (expiry never used).

    For each as-of date d:
      days_since_last_receipt = d - max(received | received <= d)
      last_receipt_gap_days   = gap between the two most recent receipts <= d
      receipts_last_7d        = count of receipts in [d-6, d]
      qty_received_last_7d    = sum quantity of those receipts
    """
    log = _normalize_order_log(invoices)
    idx = pd.to_datetime(pd.Index(dates)).normalize()
    rows: list[dict] = []

    for d in idx:
        d = pd.Timestamp(d).normalize()
        past = log[log["received_date"] <= d]
        if past.empty:
            days_since = _NO_RECEIPT_SENTINEL
            gap = _NO_RECEIPT_SENTINEL
        else:
            uniq_dates = past["received_date"].drop_duplicates().sort_values()
            last = uniq_dates.iloc[-1]
            days_since = float((d - last).days)
            if len(uniq_dates) >= 2:
                gap = float((uniq_dates.iloc[-1] - uniq_dates.iloc[-2]).days)
            else:
                gap = days_since  # only one receipt so far

        window_start = d - pd.Timedelta(days=6)
        recent = log[
            (log["received_date"] >= window_start) & (log["received_date"] <= d)
        ]
        rows.append(
            {
                "ds": d,
                "days_since_last_receipt": days_since,
                "last_receipt_gap_days": gap,
                "receipts_last_7d": float(len(recent)),
                "qty_received_last_7d": float(recent["quantity"].sum()),
            }
        )

    return pd.DataFrame(rows)


# Back-compat alias used by older call sites during rename
build_invoice_features = build_order_pattern_features
