"""Unit tests for L+C window + 80% case fill."""

from api.services.reorder_engine import compute_line_reorder


def test_target_uses_full_l_plus_c_window():
    out = compute_line_reorder(
        available=5,
        ads=2.0,
        demand_std=0.6,
        lead_days=4,
        cover_days=13,
        x_days=17,
        p50_full=34,
        p90_full=40,
        stored_horizon=17,
        box_qty=1,
        effective_days=17,
    )
    assert out["lead_demand_ads"] == 8.0  # 2 * 4
    assert out["cover_demand_ads"] == 26.0  # 2 * 13
    assert out["p90_demand"] == 40
    assert out["ai_target_qty"] >= 40
    assert out["raw_qty_to_order"] == round(out["ai_target_qty"] - 5, 2)


def test_eighty_percent_case_fill_skips_small_need():
    out = compute_line_reorder(
        available=0,
        ads=1.0,
        demand_std=0.3,
        lead_days=4,
        cover_days=3,
        x_days=7,
        p50_full=7,
        p90_full=7,
        stored_horizon=7,
        box_qty=20,
        effective_days=7,
    )
    # raw need capped by target; with p90=7 need=7 → < 80% of 20 → 0
    assert out["qty_to_order"] == 0


def test_eighty_percent_case_fill_takes_case_when_enough():
    out = compute_line_reorder(
        available=0,
        ads=3.0,
        demand_std=0.5,
        lead_days=4,
        cover_days=3,
        x_days=7,
        p50_full=20,
        p90_full=20,
        stored_horizon=7,
        box_qty=20,
        effective_days=7,
    )
    assert out["qty_to_order"] == 20
    assert out["cases_to_order"] == 1
