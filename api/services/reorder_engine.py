"""
Vendor reorder math — ADS, safety stock, ROP (AI min), ML cover, pack/cases.

Window X = L + C:
  - During lead L: stock will keep selling before the truck arrives
  - Cover C: how many days that delivery should last after arrival
  - AI target = max(P90 for X with uplift, ADS×X + SS)
  - Order = max(0, target − on-hand) → case round (80% fill)

ROP / AI min still uses L only (ADS×L + SS) as the low-stock flag.
"""

from __future__ import annotations

from typing import Any

from v2.inventory_math.pack_size import (
    normalize_pack_size,
    pack_units_ordered,
    round_up_to_pack,
)
from v2.inventory_math.reorder_point import calculate_dynamic_reorder_point
from v2.inventory_math.safety_stock import calculate_safety_stock

# Recommend a case only if raw need fills this share of the pack.
CASE_FILL_RATIO = 0.80


def scale_forecast(value: float, from_days: int, to_days: int) -> float:
    if from_days <= 0 or to_days <= 0:
        return 0.0
    if to_days == from_days:
        return float(value)
    return float(value) * (float(to_days) / float(from_days))


def compute_line_reorder(
    *,
    available: float,
    ads: float,
    demand_std: float,
    lead_days: int,
    cover_days: int,
    x_days: int,
    p50_full: float,
    p90_full: float,
    stored_horizon: int,
    box_qty: int,
    effective_days: float,
    uplift_multiplier: float = 1.0,
    service_level: float = 0.95,
    case_fill_ratio: float = CASE_FILL_RATIO,
) -> dict[str, Any]:
    """Return all sizing fields for one SKU.

    ``effective_days`` should be X = L+C (optionally expiry-capped).
    """
    lead = max(int(lead_days), 0)
    cover = max(int(cover_days), 0)
    x = max(int(x_days), 1)
    eff = max(float(effective_days), 0.0)
    pack = normalize_pack_size(box_qty)
    ads_f = max(float(ads), 0.0)
    std_f = max(float(demand_std), 0.0)

    # --- Lead-time pieces (what sells before truck arrives) ---
    ss_lead = (
        calculate_safety_stock(ads_f, std_f, max(lead, 1), service_level=service_level)
        if ads_f > 0 and lead > 0
        else 0.0
    )
    rop = (
        calculate_dynamic_reorder_point(ads_f, float(lead), ss_lead)
        if ads_f > 0 and lead > 0
        else 0.0
    )
    lead_demand_ads = round(ads_f * lead, 4) if lead > 0 else 0.0
    lead_demand_p50 = (
        scale_forecast(p50_full, stored_horizon, max(lead, 1)) if lead > 0 else 0.0
    )

    # --- Full window X = L+C (lead burn + cover after arrival) ---
    ss_x = (
        calculate_safety_stock(ads_f, std_f, max(eff, 1.0), service_level=service_level)
        if ads_f > 0 and eff > 0
        else 0.0
    )
    ads_cover = round(ads_f * eff + ss_x, 2) if eff > 0 else 0.0

    if eff <= 0:
        p50_x = 0.0
        p90_x = 0.0
    else:
        h = int(max(round(eff), 1))
        p50_x = scale_forecast(p50_full, stored_horizon, h)
        p90_x = scale_forecast(p90_full, stored_horizon, h)

    # Cover-only slice (for transparency in response)
    cover_eff = max(eff - float(lead), 0.0) if eff > 0 else float(cover)
    if cover_eff > 0:
        cover_h = int(max(round(cover_eff), 1))
        cover_demand_p90 = scale_forecast(p90_full, stored_horizon, cover_h)
        cover_demand_ads = round(ads_f * cover_eff, 4)
    else:
        cover_demand_p90 = 0.0
        cover_demand_ads = 0.0

    ai_target = round(max(float(p90_x), float(ads_cover)), 2)
    below_rop = bool(available < rop) if rop > 0 else bool(available <= 0 and ai_target > 0)

    raw_need = max(0.0, ai_target - available)
    qty_units = float(
        round_up_to_pack(raw_need, pack, min_fill_ratio=case_fill_ratio)
    )
    did_round = abs(qty_units - raw_need) > 1e-6
    cases = float(pack_units_ordered(int(qty_units), pack)) if qty_units > 0 else 0.0

    projected_at_arrival = max(0.0, available - lead_demand_p50)

    return {
        "ads": round(ads_f, 4),
        "demand_std": round(std_f, 4),
        "safety_stock": float(ss_lead),
        "reorder_point": float(rop),
        "below_reorder_point": below_rop,
        "lead_demand_ads": float(lead_demand_ads),
        "lead_demand_p50": round(float(lead_demand_p50), 2),
        "cover_demand_ads": float(cover_demand_ads),
        "cover_demand_p90": round(float(cover_demand_p90), 2),
        "ads_cover_qty": float(ads_cover),
        "uplift_multiplier": float(uplift_multiplier or 1.0),
        "p50_demand": round(p50_x, 2),
        "p90_demand": round(p90_x, 2),
        "ai_target_qty": float(ai_target),
        "projected_stock_required": float(ai_target),
        "projected_stock_at_arrival": round(projected_at_arrival, 2),
        "raw_qty_to_order": round(raw_need, 2),
        "qty_before_box_round": round(raw_need, 2),
        "qty_to_order": float(qty_units),
        "cases_to_order": float(cases),
        "box_rounded": did_round,
        "p90_eff": float(p90_x),
        "p50_eff": float(p50_x),
        "cover_days_used": float(eff),
    }
