"""
Exhaustive reorder math contract.

Each formula is checked against independent hand math on many random cases.
Column field names must match the formulas below (no silent mismatches).

Official formulas (source of truth):
  ADS / day       = units_sold_90d / 90
  ADS × X         = ADS × X                         (sanity only)
  Lead Demand (L) = ADS × L
  Cover days C    = X − L                           (after truck arrives)
  Cover Demand(C) = ceil(ADS × C)
  SS(L)           = Z × σ × √L                      (Z≈1.65 @ 95%)
  SS(C)           = Z × σ × √C
  ROP             = ADS×L + SS(L)                   (trigger only)
  Stock@Arrival   = max(0, OH − ADS×L)              (OH floored at 0 for math)
  ADS Cover Qty   = ceil(ADS×C + SS(C))
  AI Cover Target = ceil(ADS×C×uplift + SS(C))
  Desired Stock   = AI Cover Target (optional max cap; 0 if ADS≈0)
  Raw need        = max(0, Desired − Stock@Arrival)
  Qty to Order    = ceil(raw_need / pack) × pack
  Dead stock      = ADS≈0 → SKIP, qty=0 (even if OH=0 or ML P50 huge)
"""

from __future__ import annotations

import math
from itertools import product

import numpy as np
import pytest

from api.services.reorder_engine import compute_line_reorder
from v2.inventory_math.reorder_point import calculate_dynamic_reorder_point
from v2.inventory_math.safety_stock import calculate_safety_stock

Z95 = 1.65
N = 50  # cases per formula family


def _ceil(v: float) -> float:
    if v <= 1e-9:
        return 0.0
    return float(math.ceil(v - 1e-9))


def _hand(
    *,
    oh: float,
    ads: float,
    std: float,
    lead: int,
    cover: int,
    x: int,
    pack: int,
    uplift: float,
    wecomm_max: float = 0.0,
) -> dict[str, float | str | bool]:
    """Independent hand calculation — must match engine outputs.

    Cover days used = X − L (engine uses effective_days − lead).
    """
    del cover  # cover is implied by X−L when effective window is X
    ads = max(ads, 0.0)
    oh_math = max(oh, 0.0)
    cover_eff = max(x - lead, 0)

    if ads <= 1e-6:
        at_arr = oh_math if lead <= 0 else max(0.0, oh_math)
        return {
            "ads": 0.0,
            "ads_times_x": 0.0,
            "lead_demand_ads": 0.0,
            "cover_demand_ads": 0.0,
            "safety_stock": 0.0,
            "safety_stock_cover": 0.0,
            "reorder_point": 0.0,
            "ads_cover_qty": 0.0,
            "ai_target_qty": 0.0,
            "desired_stock": 0.0,
            "projected_stock_at_arrival": round(at_arr, 2),
            "qty_to_order": 0.0,
            "cases_to_order": 0.0,
            "line_action": "SKIP",
            "skip_dead_stock": True,
        }

    if std <= 0:
        sigma = ads * 0.3
    else:
        sigma = min(std, ads * 2.0)

    ss_l = round(Z95 * sigma * math.sqrt(max(lead, 1)), 2) if lead > 0 else 0.0
    ss_c = (
        round(Z95 * sigma * math.sqrt(max(cover_eff, 1)), 2) if cover_eff > 0 else 0.0
    )
    lead_d = round(ads * lead, 4) if lead > 0 else 0.0
    cover_d = _ceil(ads * cover_eff) if cover_eff > 0 else 0.0
    rop = round(lead_d + ss_l, 2) if lead > 0 else 0.0
    at_arr = max(0.0, oh_math - ads * lead) if lead > 0 else oh_math
    ads_cover = _ceil(ads * cover_eff + ss_c) if cover_eff > 0 else 0.0
    ai = _ceil(ads * cover_eff * uplift + ss_c) if cover_eff > 0 else 0.0
    desired = float(ai)
    if wecomm_max > 0:
        desired = _ceil(min(desired, wecomm_max)) if min(desired, wecomm_max) > 0 else 0.0
        if at_arr >= wecomm_max:
            desired = _ceil(at_arr)
    raw = max(0.0, desired - at_arr)
    pack = max(int(pack), 1)
    cases = int(math.ceil(raw / pack - 1e-9)) if raw > 1e-9 else 0
    qty = float(cases * pack)
    if wecomm_max > 0 and qty > 0:
        room = max(0.0, wecomm_max - at_arr)
        max_cases = int(math.floor(room / pack + 1e-9))
        if max_cases <= 0:
            qty, cases = 0.0, 0
        elif cases > max_cases:
            cases = max_cases
            qty = float(cases * pack)

    below_rop = bool(oh_math < rop) if rop > 0 else False
    if qty > 0:
        action = "ORDER"
    elif below_rop:
        action = "WATCH"
    else:
        action = "SKIP"

    return {
        "ads": round(ads, 4),
        "ads_times_x": round(ads * x, 4),
        "lead_demand_ads": float(lead_d),
        "cover_demand_ads": float(cover_d),
        "safety_stock": float(ss_l),
        "safety_stock_cover": float(ss_c),
        "reorder_point": float(rop),
        "ads_cover_qty": float(ads_cover),
        "ai_target_qty": float(ai),
        "desired_stock": float(desired),
        "projected_stock_at_arrival": round(at_arr, 2),
        "qty_to_order": float(qty),
        "cases_to_order": float(cases),
        "line_action": action,
        "skip_dead_stock": False,
    }


def _engine(**kw):
    lead = int(kw["lead"])
    cover = int(kw["cover"])
    x = int(kw.get("x", lead + cover))
    return compute_line_reorder(
        available=float(kw["oh"]),
        ads=float(kw["ads"]),
        demand_std=float(kw.get("std", kw["ads"] * 0.3)),
        lead_days=lead,
        cover_days=cover,
        x_days=x,
        p50_full=float(kw.get("p50", 0)),
        p90_full=float(kw.get("p90", 0)),
        stored_horizon=x,
        box_qty=int(kw.get("pack", 1)),
        effective_days=float(x),
        uplift_multiplier=float(kw.get("uplift", 1.0)),
        wecomm_max_on_hand=float(kw.get("wecomm_max", 0.0)),
    )


# ---------------------------------------------------------------------------
# 50 random cases — full column contract vs hand math
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(N))
def test_full_column_contract_random(seed: int):
    rng = np.random.default_rng(seed)
    ads = float(rng.choice([0.0, 0.1, 0.5, 1.0, 2.5, 5.0, 10.6011, 20.0]))
    oh = float(rng.choice([0, 1, 5, 10, 25, 50, 100, 200]))
    lead = int(rng.choice([1, 3, 4, 6, 7]))
    cover = int(rng.choice([3, 5, 7, 8, 10, 13, 14]))
    x = lead + cover
    pack = int(rng.choice([1, 2, 4, 6, 10, 12, 20]))
    uplift = float(rng.choice([1.0, 1.1, 1.25, 1.5]))
    std = float(ads * rng.choice([0.2, 0.3, 0.5, 1.0, 2.5]))
    wecomm_max = float(rng.choice([0.0, 0.0, 0.0, 10.0, 50.0, 100.0]))

    eng = _engine(
        oh=oh, ads=ads, std=std, lead=lead, cover=cover, x=x,
        pack=pack, uplift=uplift, wecomm_max=wecomm_max, p50=9999, p90=9999,
    )
    hand = _hand(
        oh=oh, ads=ads, std=std, lead=lead, cover=cover, x=x,
        pack=pack, uplift=uplift, wecomm_max=wecomm_max,
    )

    for key in (
        "ads",
        "ads_times_x",
        "lead_demand_ads",
        "cover_demand_ads",
        "safety_stock",
        "safety_stock_cover",
        "reorder_point",
        "ads_cover_qty",
        "ai_target_qty",
        "desired_stock",
        "projected_stock_at_arrival",
        "qty_to_order",
        "cases_to_order",
        "skip_dead_stock",
    ):
        assert eng[key] == pytest.approx(hand[key], abs=1e-6), (
            f"seed={seed} key={key} eng={eng[key]} hand={hand[key]} "
            f"ads={ads} oh={oh} L={lead} C={cover} pack={pack} uplift={uplift}"
        )

    # ML must NOT drive qty (reference only)
    if ads > 1e-6:
        assert eng["ai_target_qty"] == eng["desired_stock"] or wecomm_max > 0
    assert eng["qty_to_order"] == hand["qty_to_order"]


# ---------------------------------------------------------------------------
# 50 cases — each named formula in isolation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("i", range(N))
def test_ads_times_x_is_ads_times_full_window(i: int):
    ads = 0.1 + i * 0.37
    x = 7 + (i % 20)
    out = _engine(oh=0, ads=ads, lead=3, cover=x - 3, x=x, pack=1)
    assert out["ads_times_x"] == pytest.approx(ads * x, abs=1e-6)


@pytest.mark.parametrize("i", range(N))
def test_lead_demand_is_ads_times_l(i: int):
    ads = 0.2 + i * 0.15
    lead = 1 + (i % 10)
    out = _engine(oh=0, ads=ads, lead=lead, cover=7, x=lead + 7, pack=1)
    assert out["lead_demand_ads"] == pytest.approx(ads * lead, abs=1e-6)


@pytest.mark.parametrize("i", range(N))
def test_cover_demand_is_ceil_ads_times_c(i: int):
    ads = 0.11 + i * 0.09
    cover = 3 + (i % 12)
    out = _engine(oh=0, ads=ads, lead=4, cover=cover, x=4 + cover, pack=1)
    assert out["cover_demand_ads"] == _ceil(ads * cover)


@pytest.mark.parametrize("i", range(N))
def test_rop_is_ads_l_plus_ss_l(i: int):
    ads = 0.5 + (i % 20) * 0.4
    lead = 2 + (i % 8)
    std = ads * 0.3
    out = _engine(oh=0, ads=ads, std=std, lead=lead, cover=8, x=lead + 8, pack=1)
    ss = calculate_safety_stock(ads, min(std, ads * 2.0), lead)
    rop = calculate_dynamic_reorder_point(ads, float(lead), ss)
    assert out["safety_stock"] == pytest.approx(ss, abs=1e-6)
    assert out["reorder_point"] == pytest.approx(rop, abs=1e-6)
    assert out["reorder_point"] == pytest.approx(ads * lead + ss, abs=0.02)


@pytest.mark.parametrize("i", range(N))
def test_stock_at_arrival_burns_ads_times_l(i: int):
    ads = 1.0 + (i % 10) * 0.5
    lead = 3 + (i % 5)
    oh = float(10 + i * 3)
    out = _engine(oh=oh, ads=ads, lead=lead, cover=7, x=lead + 7, pack=1)
    assert out["projected_stock_at_arrival"] == pytest.approx(
        max(0.0, oh - ads * lead), abs=1e-6
    )


@pytest.mark.parametrize("i", range(N))
def test_ai_target_is_ceil_ads_c_uplift_plus_ss(i: int):
    ads = 0.8 + (i % 15) * 0.3
    cover = 5 + (i % 10)
    uplift = 1.0 + (i % 5) * 0.1
    std = ads * 0.3
    out = _engine(
        oh=0, ads=ads, std=std, lead=4, cover=cover, x=4 + cover,
        pack=1, uplift=uplift,
    )
    sigma = min(std, ads * 2.0)
    ss_c = calculate_safety_stock(ads, sigma, cover)
    expected = _ceil(ads * cover * uplift + ss_c)
    assert out["ai_target_qty"] == expected
    assert out["desired_stock"] == expected


@pytest.mark.parametrize("i", range(N))
def test_qty_rounds_up_to_full_cases(i: int):
    ads = 2.0
    pack = 1 + (i % 24)
    out = _engine(oh=0, ads=ads, lead=4, cover=10, x=14, pack=pack)
    if out["qty_to_order"] > 0:
        assert out["qty_to_order"] % pack == 0
        assert out["cases_to_order"] == out["qty_to_order"] / pack
        assert out["cases_to_order"] == float(int(out["cases_to_order"]))


@pytest.mark.parametrize("i", range(N))
def test_dead_stock_never_orders_even_with_huge_ml(i: int):
    """ADS=0 must SKIP — this is the DryApricots failure mode."""
    out = _engine(
        oh=0 if i % 2 == 0 else float(i),
        ads=0.0,
        std=0.0,
        lead=6,
        cover=8,
        x=14,
        pack=1,
        p50=1000 + i * 10,
        p90=2000 + i * 10,
    )
    assert out["skip_dead_stock"] is True
    assert out["qty_to_order"] == 0.0
    assert out["desired_stock"] == 0.0
    assert out["ads_times_x"] == 0.0
    assert out["line_action"] == "SKIP"


# ---------------------------------------------------------------------------
# Exact DryApricots regression (the 100-unit bug)
# ---------------------------------------------------------------------------

def test_apricot_fake_ads_would_order_100_but_real_zero_skips():
    """Reproduce the bad path, then prove the good path."""
    # BAD path (what UI showed): invented ADS 10.6011 → ORDER 100
    bad = _engine(
        oh=0, ads=10.6011, std=10.6011 * 0.3,
        lead=6, cover=8, x=14, pack=1, uplift=1.0,
    )
    assert bad["qty_to_order"] == 100.0
    assert bad["reorder_point"] == pytest.approx(76.46, abs=0.05)
    assert bad["ads_times_x"] == pytest.approx(10.6011 * 14, abs=1e-3)

    # GOOD path: real POS ADS=0 → must SKIP (never order 100)
    good = _engine(
        oh=0, ads=0.0, std=0.0,
        lead=6, cover=8, x=14, pack=1,
        p50=148.0, p90=167.0,  # inflated ML must be ignored
    )
    assert good["qty_to_order"] == 0.0
    assert good["line_action"] == "SKIP"
    assert good["ads_times_x"] == 0.0


def test_negative_oh_floored_for_order_math_only():
    """Engine receives floored OH; negative display is service-layer."""
    out = _engine(oh=0, ads=2.0, lead=6, cover=8, x=14, pack=1)
    assert out["projected_stock_at_arrival"] == 0.0
    assert out["qty_to_order"] > 0


def test_column_formula_map_documented():
    """Sanity: key field names used by UI/Excel exist on engine output."""
    out = _engine(oh=5, ads=2.0, lead=6, cover=8, x=14, pack=1)
    required = {
        "ads",  # ADS / day
        "ads_times_x",  # ADS × X
        "lead_demand_ads",  # Lead Demand (L) = ADS×L
        "cover_demand_ads",  # Cover Demand (C) = ceil(ADS×C)
        "safety_stock",  # SS(L)
        "safety_stock_cover",  # SS(C)
        "reorder_point",  # ROP = ADS×L+SS(L)
        "ads_cover_qty",  # ceil(ADS×C+SS(C))
        "ai_target_qty",  # ceil(ADS×C×uplift+SS(C))
        "desired_stock",
        "projected_stock_at_arrival",  # max(0, OH−ADS×L)
        "qty_to_order",
        "cases_to_order",
    }
    assert required.issubset(out.keys())


@pytest.mark.parametrize(
    "ads,lead,cover,x",
    list(product([0.0, 1.0, 2.5, 10.6011], [4, 6], [7, 8], [11, 14])),
)
def test_ads_times_x_not_confused_with_cover_or_qty(ads, lead, cover, x):
    """ADS×X is sanity only — order sizes to cover_eff = X−L, not full X."""
    out = _engine(oh=0, ads=ads, lead=lead, cover=cover, x=x, pack=1)
    cover_eff = max(x - lead, 0)
    if ads <= 1e-6:
        assert out["ads_times_x"] == 0.0
        assert out["qty_to_order"] == 0.0
        assert out["desired_stock"] == 0.0
        return
    assert out["ads_times_x"] == pytest.approx(ads * x, abs=1e-6)
    assert out["cover_demand_ads"] == _ceil(ads * cover_eff)
    assert out["cover_days_used"] == float(cover_eff)
    assert out["desired_stock"] == out["ai_target_qty"]
