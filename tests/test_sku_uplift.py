"""Per-SKU calendar uplift — each product uses its own weekend/festival pattern."""

from datetime import date

import pandas as pd

from v2.forecasting.calendar_uplift import (
    explain_sku_uplift_for_cover_window,
    learn_sku_uplift_factors,
)


def _sku_days(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": pd.Timestamp(d), "quantity": q} for d, q in rows]
    )


def test_snack_spikes_weekend_rice_stays_flat():
    # Snack: weak weekdays, strong weekends
    snack = []
    rice = []
    # 4 weeks of Mon-Sun starting 2026-06-01 (Monday)
    start = date(2026, 6, 1)
    for w in range(4):
        for dow in range(7):
            d = date(2026, 6, 1 + w * 7 + dow)
            if dow < 5:  # weekday
                snack.append((d.isoformat(), 1.0))
                rice.append((d.isoformat(), 5.0))
            else:  # weekend
                snack.append((d.isoformat(), 5.0))
                rice.append((d.isoformat(), 5.0))

    snack_f = learn_sku_uplift_factors(_sku_days(snack))
    rice_f = learn_sku_uplift_factors(_sku_days(rice))

    assert snack_f.get("day_type:Weekend", 1.0) > 1.2
    assert rice_f.get("day_type:Weekend", 1.0) <= 1.15


def test_explain_sku_uplift_boosts_only_spiky_item():
    snack = []
    for w in range(4):
        for dow in range(7):
            d = date(2026, 6, 1 + w * 7 + dow)
            snack.append((d.isoformat(), 1.0 if dow < 5 else 6.0))

    # Cover window that includes a Saturday
    expl = explain_sku_uplift_for_cover_window(
        _sku_days(snack),
        cover_days=7,
        from_date=date(2026, 7, 24),  # Friday → includes Sat/Sun
    )
    assert expl["uplift_factor"] > 1.0
    assert "per-item" in expl["summary"].lower() or "own history" in expl["summary"].lower()
