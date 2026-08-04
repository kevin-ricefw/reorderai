"""Cross-checks: cover ceil, full-case orders, ROP trigger, no min floor."""

from api.services.reorder_engine import compute_line_reorder


def _base(**kwargs):
    defaults = dict(
        available=0,
        ads=2.0,
        demand_std=0.5,
        lead_days=4,
        cover_days=13,
        x_days=17,
        p50_full=34,
        p90_full=34,
        stored_horizon=17,
        box_qty=1,
        effective_days=17,
        uplift_multiplier=1.0,
        wecomm_min_on_hand=0.0,
        wecomm_max_on_hand=0.0,
    )
    defaults.update(kwargs)
    return compute_line_reorder(**defaults)


def test_cover_and_desired_are_whole_units():
    out = _base(ads=0.4, cover_days=13, lead_days=4, x_days=17, effective_days=17)
    assert out["cover_demand_ads"] == float(int(out["cover_demand_ads"]))
    assert out["ai_target_qty"] == float(int(out["ai_target_qty"]))
    assert out["desired_stock"] == float(int(out["desired_stock"]))
    assert out["cover_demand_ads"] >= 0.4 * 13 - 1e-6
    assert abs(out["ads_times_x"] - 0.4 * 17) < 1e-6


def test_ads_times_x_sanity_baseline():
    out = _base(ads=2.0, x_days=14, lead_days=6, cover_days=8, effective_days=14)
    assert out["ads_times_x"] == 28.0
    dead = _base(ads=0.0, x_days=14, lead_days=6, cover_days=8, effective_days=14)
    assert dead["ads_times_x"] == 0.0


def test_rop_not_used_as_min_floor():
    out = _base()
    assert out["min_on_hand"] == 0.0
    assert out["desired_stock"] == out["ai_target_qty"]


def test_wecomm_min_ignored():
    base = _base()
    with_min = _base(wecomm_min_on_hand=50.0)
    assert with_min["desired_stock"] == base["desired_stock"]
    assert with_min["min_on_hand"] == 0.0


def test_full_cases_never_fractional():
    out = _base(
        available=3,
        ads=0.4,
        demand_std=0.8,
        box_qty=20,
        lead_days=4,
        cover_days=13,
        x_days=17,
        effective_days=17,
    )
    assert out["raw_qty_to_order"] > 0
    assert out["cases_to_order"] == float(int(out["cases_to_order"]))
    assert out["cases_to_order"] >= 1
    assert out["qty_to_order"] == out["cases_to_order"] * 20
    assert out["line_action"] == "ORDER"


def test_pack_one_still_whole_units():
    out = _base(box_qty=1, available=0, ads=2.0)
    assert out["qty_to_order"] == out["desired_stock"]
    assert out["cases_to_order"] == out["qty_to_order"]


def test_zero_oh_orders_cover_not_lead():
    out = _base(available=0)
    assert out["ai_target_qty"] < 2.0 * 17 + 5
    assert out["line_action"] == "ORDER"
    assert out["urgency"] == "stockout"


def test_positive_stock_burns_during_lead():
    out = _base(available=20, box_qty=1)
    assert out["projected_stock_at_arrival"] == 12.0
    assert out["raw_qty_to_order"] == round(out["desired_stock"] - 12.0, 2)


def test_dead_stock_skipped():
    out = _base(ads=0.0, demand_std=0.0, p50_full=0, p90_full=0)
    assert out["skip_dead_stock"]
    assert out["qty_to_order"] == 0
    assert out["line_action"] == "SKIP"


def test_wecomm_max_caps():
    out = _base(wecomm_max_on_hand=10.0, box_qty=1)
    assert out["desired_stock"] <= 10.0
    assert out["max_capped_target"]


def test_uplift_scales_cover():
    base = _base(lead_days=3, cover_days=4, x_days=7, effective_days=7, stored_horizon=7)
    up = _base(
        lead_days=3,
        cover_days=4,
        x_days=7,
        effective_days=7,
        stored_horizon=7,
        uplift_multiplier=1.5,
    )
    assert up["ai_target_qty"] >= base["ai_target_qty"]


def test_enough_stock_skips():
    out = _base(available=200)
    assert out["qty_to_order"] == 0
    assert out["line_action"] == "SKIP"


def test_ml_does_not_drive_qty():
    out = _base(p90_full=400)
    assert out["ai_target_qty"] < 100
    assert out["p90_demand"] == 400


def test_aashirvaad_like_full_case():
    out = _base(
        available=0,
        ads=0.4762,
        demand_std=0.9,
        lead_days=4,
        cover_days=13,
        x_days=17,
        effective_days=17,
        uplift_multiplier=1.0909,
        box_qty=4,
    )
    assert out["cover_demand_ads"] == float(int(out["cover_demand_ads"]))
    assert out["qty_to_order"] % 4 == 0
    assert out["cases_to_order"] == float(int(out["cases_to_order"]))
    assert out["line_action"] == "ORDER"
