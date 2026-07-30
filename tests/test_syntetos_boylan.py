"""Tests for Syntetos-Boylan ADI / CV² classification."""

from datetime import date

import pandas as pd

from v2.analytics.syntetos_boylan import (
    ADI_THRESHOLD,
    CLASS_ERRATIC,
    CLASS_INTERMITTENT,
    CLASS_LUMPY,
    CLASS_SMOOTH,
    classify_adi_cv2,
    classify_skus_syntetos_boylan,
)


def test_classify_adi_cv2_quadrants():
    assert classify_adi_cv2(1.0, 0.2) == CLASS_SMOOTH
    assert classify_adi_cv2(ADI_THRESHOLD, 0.2) == CLASS_INTERMITTENT
    assert classify_adi_cv2(1.0, 0.6) == CLASS_ERRATIC
    assert classify_adi_cv2(2.0, 0.6) == CLASS_LUMPY


def test_classify_skus_smooth_vs_intermittent():
    # 10 calendar days
    # SKU A: sells every day qty=5 → ADI≈1, CV²=0 → Smooth
    # SKU B: sells day 1 and day 8 only, qty=5 each → ADI=7, CV²=0 → Intermittent
    rows = []
    for d in range(1, 11):
        rows.append(
            {
                "date": date(2026, 1, d),
                "upc": "A",
                "description": "Daily item",
                "quantity": 5,
            }
        )
    rows.append(
        {"date": date(2026, 1, 1), "upc": "B", "description": "Sparse item", "quantity": 5}
    )
    rows.append(
        {"date": date(2026, 1, 8), "upc": "B", "description": "Sparse item", "quantity": 5}
    )
    sales = pd.DataFrame(rows)

    out = classify_skus_syntetos_boylan(
        sales,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 10),
    )
    by_upc = out.set_index("upc")
    assert by_upc.loc["A", "demand_class"] == CLASS_SMOOTH
    assert by_upc.loc["B", "demand_class"] == CLASS_INTERMITTENT
    assert by_upc.loc["A", "adi"] < ADI_THRESHOLD
    assert by_upc.loc["B", "adi"] >= ADI_THRESHOLD
