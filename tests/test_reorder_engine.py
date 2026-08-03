"""Cross-checks for pro reorder math (ROP trigger, cover qty, min/max, dead stock)."""

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


def test_cover_target_not_driven_by_ml():
    out = _base(p90_full=400, available=5)
    assert out["ai_target_qty"] == out["ads_cover_qty"]
    assert out["ai_target_qty"] < 100
    assert out["p90_demand"] == 400


def test_rop_is_trigger_not_order_floor():
    out = _base()
    assert out["min_on_hand_source"] == "none"
    assert out["min_on_hand"] == 0.0
    # desired equals cover, not ROP
    assert out["desired_stock"] == out["ai_target_qty"]
    assert out["reorder_point"] > 0
    assert out["desired_stock"] > out["reorder_point"]


def test_zero_oh_orders_cover_not_lead():
    out = _base(available=0)
    assert out["raw_qty_to_order"] == out["desired_stock"]
    assert out["ai_target_qty"] < 2.0 * 17 + 5
    assert out["line_action"] == "ORDER"
    assert out["urgency"] == "stockout"


def test_positive_stock_burns_during_lead():
    out = _base(available=20)
    assert out["projected_stock_at_arrival"] == 12.0
    assert out["raw_qty_to_order"] == round(out["desired_stock"] - 12.0, 2)


def test_wecomm_min_raises_desired():
    out = _base(wecomm_min_on_hand=50.0)
    assert out["min_on_hand_source"] == "wecomm"
    assert out["desired_stock"] == 50.0
    assert out["min_raised_target"]
    assert out["raw_qty_to_order"] == 50.0


def test_wecomm_max_caps_desired_anti_overstock():
    out = _base(wecomm_min_on_hand=0.0, wecomm_max_on_hand=10.0)
    assert out["desired_stock"] == 10.0
    assert out["max_capped_target"]
    assert out["raw_qty_to_order"] == 10.0


def test_already_above_max_orders_zero():
    out = _base(available=100, wecomm_max_on_hand=20.0, ads=1.0)
    # burns 4 during L → 96 at arrival still >> max 20
    assert out["qty_to_order"] == 0
    assert out["line_action"] in ("SKIP", "WATCH")


def test_low_wecomm_min_does_not_change_cover():
    base = _base()
    low = _base(wecomm_min_on_hand=5.0)
    assert low["desired_stock"] == base["ai_target_qty"]
    assert low["raw_qty_to_order"] == base["raw_qty_to_order"]


def test_dead_stock_skipped():
    out = _base(ads=0.0, demand_std=0.0, p50_full=0, p90_full=0, available=0)
    assert out["skip_dead_stock"]
    assert out["qty_to_order"] == 0
    assert out["line_action"] == "SKIP"
    assert out["urgency"] == "skip"


def test_dead_stock_with_wecomm_min_restocks():
    out = _base(ads=0.0, demand_std=0.0, p50_full=0, p90_full=0, wecomm_min_on_hand=12.0)
    assert not out["skip_dead_stock"]
    assert out["desired_stock"] == 12.0
    assert out["qty_to_order"] == 12.0
    assert out["line_action"] == "ORDER"


def test_uplift_scales_cover_sales_only():
    base = _base(lead_days=3, cover_days=4, x_days=7, effective_days=7, stored_horizon=7)
    up = _base(
        lead_days=3,
        cover_days=4,
        x_days=7,
        effective_days=7,
        stored_horizon=7,
        uplift_multiplier=1.5,
    )
    assert base["expected_sales_x"] == 8.0
    assert up["uplifted_expected_x"] == 12.0
    assert up["safety_stock_x"] == base["safety_stock_x"]


def test_case_fill_skips_tiny_need():
    out = _base(
        ads=1.0,
        demand_std=0.3,
        lead_days=4,
        cover_days=3,
        x_days=7,
        effective_days=7,
        stored_horizon=7,
        box_qty=20,
        p50_full=7,
        p90_full=7,
    )
    assert out["desired_stock"] < 16
    assert out["qty_to_order"] == 0
    # below ROP with OH=0 → WATCH (don't hide from buyer)
    assert out["line_action"] == "WATCH"
    assert out["below_reorder_point"]


def test_case_fill_takes_full_case():
    out = _base(
        ads=5.0,
        demand_std=0.5,
        lead_days=4,
        cover_days=3,
        x_days=7,
        effective_days=7,
        stored_horizon=7,
        box_qty=20,
        p50_full=20,
        p90_full=20,
    )
    assert out["qty_to_order"] == 20
    assert out["cases_to_order"] == 1
    assert out["line_action"] == "ORDER"


def test_slow_seller_not_inflated_by_ml():
    out = _base(
        available=7,
        ads=0.3333,
        demand_std=0.9783,
        lead_days=5,
        cover_days=14,
        x_days=19,
        effective_days=19,
        stored_horizon=21,
        box_qty=8,
        p50_full=49.56,
        p90_full=68.07,
    )
    assert out["ai_target_qty"] == out["ads_cover_qty"]
    assert out["cases_to_order"] <= 2


def test_spike_std_capped():
    out = _base(
        available=1,
        ads=0.8889,
        demand_std=6.9796,
        lead_days=5,
        cover_days=14,
        x_days=19,
        effective_days=19,
        stored_horizon=21,
        box_qty=48,
        p50_full=3,
        p90_full=6,
    )
    assert out["demand_std"] <= 0.8889 * 2.0 + 1e-6


def test_days_of_supply():
    out = _base(available=10, ads=2.0)
    assert out["days_of_supply"] == 5.0


def test_urgency_critical_when_far_below_rop():
    out = _base(available=1, ads=2.0)  # ROP ≈ 8+SS
    assert out["below_reorder_point"]
    assert out["urgency"] in ("critical", "high", "stockout")


def test_enough_stock_skips_order():
    # Huge OH survives lead and covers C
    out = _base(available=200)
    assert out["qty_to_order"] == 0
    assert out["line_action"] == "SKIP"
    assert out["urgency"] == "ok"


def test_aashirvaad_like_numbers():
    """Regression: OH=0, ADS~0.48, L=4, C=13 → cover-only ~7–8, not L+C."""
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
    assert out["min_on_hand"] == 0.0
    assert out["desired_stock"] == out["ai_target_qty"]
    # Cover-C only: well below classic ADS×(L+C)×uplift + SS(X) (~15+)
    assert out["ai_target_qty"] < 0.4762 * 17 * 1.0909 + 8
    assert out["qty_to_order"] % 4 == 0
    assert 4.0 <= out["qty_to_order"] <= 16.0


def test_short_cover_does_not_inflate_via_rop_min():
    """Former bug: ROP-as-min raised short-C orders toward lead size."""
    out = _base(
        ads=2.0,
        lead_days=10,
        cover_days=3,
        x_days=13,
        effective_days=13,
        stored_horizon=13,
        p50_full=26,
        p90_full=26,
    )
    # Cover need ≈ 6+SS(C); ROP ≈ 20+SS(L). Without ROP floor, desired ≈ cover.
    assert out["desired_stock"] == out["ai_target_qty"]
    assert out["desired_stock"] < out["reorder_point"]
