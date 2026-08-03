"""
W-1 Detect Order — vendor reorder sheet.

Select vendor + L + C → for each SKU:
  ADS, safety stock, ROP (AI min), uplifted P50/P90,
  order-up-to target, units + cases.
"""

from __future__ import annotations

import os
from typing import Any

from api.repositories.detect_order_repository import DetectOrderRepository
from api.repositories.forecast_store import ForecastStore, nearest_horizon
from api.schemas.detect_order import (
    DetectOrderItem,
    DetectOrderRequest,
    DetectOrderResponse,
    VendorInfo,
)
from api.services.order_run_store import new_run_id, save_order_run
from api.services.reorder_engine import compute_line_reorder


def _ads_lookback_days() -> float:
    try:
        return max(float(os.getenv("ADS_LOOKBACK_DAYS", "90")), 1.0)
    except (TypeError, ValueError):
        return 90.0


def _vendors(repo: DetectOrderRepository) -> list[VendorInfo]:
    return [
        VendorInfo(vendor_id=str(v["vendor_id"]), vendor_name=str(v["vendor_name"]))
        for v in repo.list_vendors()
    ]


def _template_justification(item: DetectOrderItem, *, lead: int, cover: int) -> str:
    parts = [
        f"{item.description}: [{item.line_action}/{item.urgency}] "
        f"order {item.qty_to_order:g} units ({item.cases_to_order:g} cases × pack {item.box_qty}).",
        f"L{lead}+C{cover} (X={item.horizon_days}d): ROP triggers urgency; "
        f"qty covers C after arrival (≥80% case fill).",
        f"ADS={item.ads:g}/day; DOS≈{item.days_of_supply}; "
        f"lead burn ≈{item.lead_demand_ads:g}; cover ≈{item.cover_demand_ads:g}; "
        f"ROP={item.reorder_point:g}"
        f"{' [BELOW ROP]' if item.below_reorder_point else ''}; "
        f"Min={item.min_on_hand:g} ({item.min_on_hand_source})"
        f"{' [BELOW MIN]' if item.below_min_on_hand else ''}"
        f"{f'; Max={item.wecomm_max_on_hand:g}' if item.wecomm_max_on_hand else ''}.",
        f"Cover=(ADS×C×uplift)+SS(C)→{item.ai_target_qty:g}; "
        f"desired={item.desired_stock:g}; "
        f"OH={item.available_stock:g}; at arrival≈{item.projected_stock_at_arrival:g}; "
        f"raw need={item.raw_qty_to_order:g}.",
    ]
    if item.last_pallet_qty is not None:
        parts.append(f"Last invoice qty ref={item.last_pallet_qty:g}.")
    if item.validation_notes:
        parts.append("Notes: " + "; ".join(item.validation_notes))
    return " ".join(parts)


def _gpt_or_template_justification(
    item: DetectOrderItem,
    *,
    lead: int,
    cover: int,
    enabled: bool,
) -> str:
    base = _template_justification(item, lead=lead, cover=cover)
    if not enabled:
        return base
    try:
        from api.services.explain_service import explain_reorder

        answer = explain_reorder(
            "Explain why this order quantity is recommended.",
            {
                "description": item.description,
                "ads": item.ads,
                "safety_stock": item.safety_stock,
                "reorder_point": item.reorder_point,
                "ai_target_qty": item.ai_target_qty,
                "available_stock": item.available_stock,
                "p90_demand": item.p90_demand,
                "uplift_multiplier": item.uplift_multiplier,
                "qty_to_order": item.qty_to_order,
                "cases_to_order": item.cases_to_order,
                "template": base,
            },
        )
        if answer and not answer.lower().startswith("openai unavailable"):
            return answer
    except Exception:
        pass
    return base


def detect_order(req: DetectOrderRequest) -> DetectOrderResponse:
    repo = DetectOrderRepository()
    store = ForecastStore()

    lead = int(req.lead_time_days)
    cover = int(req.time_to_cover_days)
    x_days = max(lead + cover, 1)

    try:
        vendors = _vendors(repo)
    except Exception as exc:
        return DetectOrderResponse(
            ok=False,
            vendors=[],
            lead_time_days=lead,
            time_to_cover_days=cover,
            x_days=x_days,
            db_mode=repo.mode,  # type: ignore[arg-type]
            forecast_mode="stub",  # type: ignore[arg-type]
            message=(
                "Database error — reconnect the SSH tunnel to the Wecomm *store* Postgres "
                f"(tenant schema with vendors/products). Details: {exc}"
            ),
        )

    if not (req.vendor_id or req.vendor_name):
        return DetectOrderResponse(
            ok=True,
            vendors=vendors,
            lead_time_days=lead,
            time_to_cover_days=cover,
            x_days=x_days,
            db_mode=repo.mode,  # type: ignore[arg-type]
            forecast_mode=store.mode,  # type: ignore[arg-type]
            message=f"{len(vendors)} vendors available. Pass vendor_id or vendor_name.",
        )

    vendor = repo.detect_vendor(vendor_id=req.vendor_id, vendor_name=req.vendor_name)
    if vendor is None:
        return DetectOrderResponse(
            ok=False,
            vendors=vendors,
            vendor=VendorInfo(
                vendor_id=req.vendor_id or "",
                vendor_name=req.vendor_name or "",
                detected=False,
            ),
            lead_time_days=lead,
            time_to_cover_days=cover,
            x_days=x_days,
            db_mode=repo.mode,  # type: ignore[arg-type]
            forecast_mode=store.mode,  # type: ignore[arg-type]
            message="Vendor not found.",
        )

    vendor_id = str(vendor["vendor_id"])
    vendor_name = str(vendor["vendor_name"])
    vendor_info = VendorInfo(vendor_id=vendor_id, vendor_name=vendor_name, detected=True)

    raw_items = repo.fetch_vendor_items(vendor_id)
    if not raw_items:
        return DetectOrderResponse(
            ok=True,
            vendors=vendors,
            vendor=vendor_info,
            lead_time_days=lead,
            time_to_cover_days=cover,
            x_days=x_days,
            db_mode=repo.mode,  # type: ignore[arg-type]
            forecast_mode=store.mode,  # type: ignore[arg-type]
            message=(
                f"No catalog items for vendor {vendor_name}. "
                "Need rows in product_vendor."
            ),
        )

    item_ids = [str(it["item_id"]) for it in raw_items]
    available_map = repo.fetch_available_stock(item_ids)
    # Warm ADS/std once for the whole catalog
    demand_stats = store.get_demand_stats(item_ids)
    _ = nearest_horizon(x_days)

    lines: list[DetectOrderItem] = []

    for it in raw_items:
        item_id = str(it["item_id"])
        raw_on_hand = float(available_map.get(item_id, 0.0))
        oversold = max(0.0, -raw_on_hand)
        available = max(0.0, raw_on_hand)
        box_qty = max(int(it.get("box_qty") or 1), 1)
        exp_days = it.get("expiration_days_remaining")
        exp_days_f = float(exp_days) if exp_days is not None else None
        last_pallet = it.get("last_pallet_qty")
        alt_ids = [str(x) for x in (it.get("upc"), it.get("sku")) if x]
        demand_class = it.get("demand_class") or store.demand_class_for(
            item_id, alt_ids=alt_ids
        )

        fc = store.get_forecast(
            item_id,
            horizon_days=x_days,
            demand_class=str(demand_class) if demand_class else None,
            alt_ids=alt_ids,
        )
        if demand_class is None and fc.get("demand_class"):
            demand_class = fc.get("demand_class")

        st = demand_stats.get(item_id) or {}
        ads = float(fc.get("ads") if fc.get("ads") is not None else st.get("ads") or 0.0)
        demand_std = float(
            fc.get("demand_std")
            if fc.get("demand_std") is not None
            else st.get("demand_std")
            or 0.0
        )
        if demand_std <= 0 and ads > 0:
            demand_std = ads * 0.3

        # Negative OH: count oversell as sold for ADS on this request; stock = 0.
        # Do not add |negative| onto the PO qty.
        if oversold > 0:
            ads = ads + oversold / _ads_lookback_days()

        p50_full = float(fc["p50"])
        p90_full = float(fc["p90"])
        stored_h = int(fc["horizon_days"])
        uplift_m = float(fc.get("uplift_multiplier") or 1.0)
        uplift_rule = fc.get("uplift_rule")

        notes: list[str] = []
        if oversold > 0:
            notes.append(
                f"On-hand was {raw_on_hand:g}; {oversold:g} counted as sold for ADS, "
                f"stock treated as 0 for ordering (not added to PO). Cover C only."
            )

        # Order sizes to cover C after arrival; OH burns during L.
        effective_days = float(x_days)
        expiry_capped = False
        if exp_days_f is not None and effective_days > 0 and exp_days_f < effective_days:
            effective_days = max(exp_days_f, 0.0)
            expiry_capped = True
            notes.append(
                f"Window capped to {effective_days:g}d by expiration "
                f"(requested X={x_days}d = L{lead}+C{cover})."
            )

        wecomm_min = float(it.get("wecomm_min_on_hand") or 0.0)
        wecomm_max = float(it.get("wecomm_max_on_hand") or 0.0)

        calc = compute_line_reorder(
            available=available,
            ads=ads,
            demand_std=demand_std,
            lead_days=lead,
            cover_days=cover,
            x_days=x_days,
            p50_full=p50_full,
            p90_full=p90_full,
            stored_horizon=stored_h,
            box_qty=box_qty,
            effective_days=effective_days,
            uplift_multiplier=uplift_m,
            wecomm_min_on_hand=wecomm_min,
            wecomm_max_on_hand=wecomm_max,
        )
        if calc.get("skip_dead_stock"):
            notes.append("No recent demand (ADS≈0) — skipped to avoid overstock.")
        if uplift_m and float(uplift_m) > 1.0:
            notes.append(
                f"Cover sales uplift×{float(uplift_m):g}"
                f"{f' ({uplift_rule})' if uplift_rule else ''}."
            )
        if calc.get("min_raised_target"):
            notes.append(
                f"Wecomm min {float(calc.get('min_on_hand') or 0):g} raised desired "
                f"above cover {float(calc.get('ai_target_qty') or 0):g}."
            )
        if calc.get("max_capped_target"):
            notes.append(
                f"Wecomm max {wecomm_max:g} capped desired (anti-overstock)."
            )
        if calc.get("line_action") == "WATCH" and float(calc.get("qty_to_order") or 0) <= 0:
            notes.append(
                "WATCH: below ROP/min but order qty is 0 "
                "(enough at arrival, or need < 80% of a case)."
            )

        qty_box = float(calc["qty_to_order"])
        # Expiry re-cap after pack round vs desired stock at arrival
        if expiry_capped and qty_box > 0 and effective_days > 0:
            max_sellable = float(calc["projected_stock_required"])
            at_arrival = float(calc["projected_stock_at_arrival"])
            if at_arrival + qty_box > max_sellable + 1e-6:
                from v2.inventory_math.pack_size import normalize_pack_size, round_up_to_pack

                pack = normalize_pack_size(box_qty)
                capped_need = max(0.0, max_sellable - at_arrival)
                new_qty = float(round_up_to_pack(capped_need, pack))
                while new_qty > 0 and (at_arrival + new_qty) > max_sellable + 1e-6:
                    new_qty = max(0.0, new_qty - pack)
                if new_qty != qty_box:
                    notes.append(f"Expiry re-cap after pack round: {qty_box:g} → {new_qty:g}.")
                    qty_box = new_qty
                    calc["qty_to_order"] = qty_box
                    calc["cases_to_order"] = (
                        round(qty_box / pack, 2) if pack > 1 and qty_box > 0 else qty_box
                    )

        item = DetectOrderItem(
            item_id=item_id,
            upc=it.get("upc"),
            sku=it.get("sku"),
            description=str(it.get("description") or ""),
            vendor_id=vendor_id,
            demand_class=str(demand_class) if demand_class else None,
            forecast_source=str(fc.get("source") or ""),
            available_stock=available,
            last_pallet_qty=float(last_pallet) if last_pallet is not None else None,
            expiration_days_remaining=exp_days_f,
            box_qty=box_qty,
            ads=float(calc["ads"]),
            demand_std=float(calc["demand_std"]),
            safety_stock=float(calc["safety_stock"]),
            safety_stock_cover=float(calc.get("safety_stock_x") or 0.0),
            reorder_point=float(calc["reorder_point"]),
            below_reorder_point=bool(calc["below_reorder_point"]),
            wecomm_min_on_hand=float(calc.get("wecomm_min_on_hand") or 0.0),
            wecomm_max_on_hand=float(calc.get("wecomm_max_on_hand") or 0.0),
            min_on_hand=float(calc.get("min_on_hand") or 0.0),
            min_on_hand_source=str(calc.get("min_on_hand_source") or "none"),
            below_min_on_hand=bool(calc.get("below_min_on_hand")),
            desired_stock=float(calc.get("desired_stock") or calc["ai_target_qty"]),
            days_of_supply=calc.get("days_of_supply"),
            days_of_supply_after_order=calc.get("days_of_supply_after_order"),
            urgency=str(calc.get("urgency") or "ok"),
            line_action=str(calc.get("line_action") or "SKIP"),
            lead_demand_ads=float(calc["lead_demand_ads"]),
            lead_demand_p50=float(calc["lead_demand_p50"]),
            cover_demand_ads=float(calc["cover_demand_ads"]),
            cover_demand_p90=float(calc["cover_demand_p90"]),
            ads_cover_qty=float(calc["ads_cover_qty"]),
            uplift_multiplier=float(calc["uplift_multiplier"]),
            uplift_rule=str(uplift_rule) if uplift_rule else None,
            p50_demand=float(calc["p50_demand"]),
            p90_demand=float(calc["p90_demand"]),
            ai_target_qty=float(calc["ai_target_qty"]),
            horizon_days=x_days,
            forecast_horizon_used=stored_h,
            projected_stock_required=float(calc["projected_stock_required"]),
            projected_stock_at_arrival=float(calc["projected_stock_at_arrival"]),
            raw_qty_to_order=float(calc["raw_qty_to_order"]),
            qty_before_box_round=float(calc["qty_before_box_round"]),
            qty_to_order=float(calc["qty_to_order"]),
            cases_to_order=float(calc["cases_to_order"]),
            expiry_capped=expiry_capped,
            expiry_cap_days=effective_days if expiry_capped else None,
            box_rounded=bool(calc["box_rounded"]),
            validation_notes=notes,
        )
        item.justification = _gpt_or_template_justification(
            item, lead=lead, cover=cover, enabled=req.generate_justification
        )
        lines.append(item)

    # Sort: stockout/critical first, then ORDER qty desc, then WATCH
    _urg_rank = {
        "stockout": 0,
        "critical": 1,
        "high": 2,
        "medium": 3,
        "ok": 4,
        "skip": 5,
    }
    lines.sort(
        key=lambda x: (
            _urg_rank.get(x.urgency, 9),
            0 if x.line_action == "ORDER" else 1 if x.line_action == "WATCH" else 2,
            -x.qty_to_order,
            x.description or "",
        )
    )

    order_lines = [x for x in lines if x.qty_to_order > 0]
    watch_lines = [x for x in lines if x.line_action == "WATCH"]
    actionable = [x for x in lines if x.line_action in ("ORDER", "WATCH")]
    out_items = lines if req.include_zero_orders else actionable
    total_units = round(sum(x.qty_to_order for x in order_lines), 2)
    total_cases = round(sum(x.cases_to_order for x in order_lines), 2)

    run_id = new_run_id()
    response = DetectOrderResponse(
        ok=True,
        run_id=run_id,
        vendors=vendors,
        vendor=vendor_info,
        lead_time_days=lead,
        time_to_cover_days=cover,
        x_days=x_days,
        catalog_item_count=len(lines),
        item_count=len(out_items),
        order_line_count=len(order_lines),
        total_units_to_order=total_units,
        total_cases_to_order=total_cases,
        items=out_items,
        db_mode=repo.mode,  # type: ignore[arg-type]
        forecast_mode=store.mode,  # type: ignore[arg-type]
        message=(
            f"{vendor_name}: catalog {len(lines)} → ORDER {len(order_lines)} + "
            f"WATCH {len(watch_lines)} for X={x_days}d (L={lead}/C={cover}): "
            f"{total_units:g} units / {total_cases:g} cases. "
            f"ROP=trigger only; qty=cover C (+ Wecomm min/max). run_id={run_id}"
        ),
    )

    save_order_run(response.model_dump())
    return response


def get_order_run(run_id: str) -> dict[str, Any] | None:
    from api.services.order_run_store import load_order_run

    return load_order_run(run_id)
