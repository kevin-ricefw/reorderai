"""
W-1 Detect Order service — Steps 1–6 (design doc §5.3).

Nightly batch owns models; this API only:
  reads DB stock/items, reads forecast_store P50/P90,
  does arithmetic + validation, saves run_id, builds justification.
"""

from __future__ import annotations

import math
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


def _vendors(repo: DetectOrderRepository) -> list[VendorInfo]:
    return [
        VendorInfo(vendor_id=str(v["vendor_id"]), vendor_name=str(v["vendor_name"]))
        for v in repo.list_vendors()
    ]


def _round_up_to_box(qty: float, box_qty: int) -> tuple[float, bool]:
    box = max(int(box_qty or 1), 1)
    if qty <= 0:
        return 0.0, False
    rounded = float(math.ceil(qty / box) * box)
    return rounded, rounded != qty


def _scale_forecast(value: float, from_days: int, to_days: int) -> float:
    """Scale a horizon total when expiry caps coverage below the stored horizon."""
    if from_days <= 0 or to_days <= 0:
        return 0.0
    if to_days == from_days:
        return float(value)
    return float(value) * (float(to_days) / float(from_days))


def _template_justification(item: DetectOrderItem, *, lead: int, cover: int) -> str:
    parts = [
        f"{item.description}: order {item.qty_to_order:g} units.",
        f"Planning window X = lead {lead}d + cover {cover}d = {item.horizon_days}d "
        f"(forecast store horizon used: {item.forecast_horizon_used}d).",
        f"Demand class={item.demand_class or 'n/a'}; P50={item.p50_demand:g}, P90={item.p90_demand:g}.",
        f"Projected stock required (P90, after caps)={item.projected_stock_required:g}; "
        f"available={item.available_stock:g}; "
        f"projected on-hand at arrival≈{item.projected_stock_at_arrival:g}.",
        f"Raw need={item.raw_qty_to_order:g}.",
    ]
    if item.expiry_capped:
        parts.append(
            f"Expiry cap applied: only cover {item.expiry_cap_days:g} days "
            f"(expiration_days_remaining={item.expiration_days_remaining})."
        )
    if item.box_rounded:
        parts.append(
            f"Rounded up to whole box (box_qty={item.box_qty}) "
            f"from {item.qty_before_box_round:g} → {item.qty_to_order:g}."
        )
    if item.last_pallet_qty is not None:
        parts.append(f"Last pallet quantity reference={item.last_pallet_qty:g}.")
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

        ctx = {
            "description": item.description,
            "upc": item.upc,
            "demand_class": item.demand_class,
            "current_stock": item.available_stock,
            "lead_time_days": lead,
            "days_to_cover": cover,
            "forecast_horizon_days": item.horizon_days,
            "ml_forecast_demand": item.p90_demand,
            "order_qty": item.qty_to_order,
            "pack_size": item.box_qty,
            "order_math_note": base,
            "projected_stock_required": item.projected_stock_required,
            "projected_stock_at_arrival": item.projected_stock_at_arrival,
            "expiry_capped": item.expiry_capped,
            "last_pallet_qty": item.last_pallet_qty,
        }
        answer = explain_reorder(
            "Explain in 2-4 short sentences why this order quantity is recommended. "
            "Use only the provided numbers.",
            ctx,
        )
        if answer and not answer.lower().startswith("openai"):
            return answer.strip()
    except Exception:
        pass
    return base


def detect_order(req: DetectOrderRequest) -> DetectOrderResponse:
    repo = DetectOrderRepository()
    store = ForecastStore()
    vendors = _vendors(repo)

    lead = int(req.lead_time_days)
    cover = int(req.time_to_cover_days)
    x_days = max(lead + cover, 1)

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

    # Step 1 — fetch items
    raw_items = repo.fetch_vendor_items(vendor_id)
    item_ids = [str(it["item_id"]) for it in raw_items]

    # Step 2 — fetch available stock
    available_map = repo.fetch_available_stock(item_ids)

    horizon_used = nearest_horizon(x_days)
    lines: list[DetectOrderItem] = []

    for it in raw_items:
        item_id = str(it["item_id"])
        available = float(available_map.get(item_id, 0.0))
        box_qty = max(int(it.get("box_qty") or 1), 1)
        exp_days = it.get("expiration_days_remaining")
        exp_days_f = float(exp_days) if exp_days is not None else None
        last_pallet = it.get("last_pallet_qty")
        demand_class = it.get("demand_class")

        # Step 3 — read forecast for X days (nearest stored horizon)
        fc = store.get_forecast(
            item_id, horizon_days=x_days, demand_class=str(demand_class) if demand_class else None
        )
        p50_full = float(fc["p50"])
        p90_full = float(fc["p90"])
        stored_h = int(fc["horizon_days"])

        # Scale stored horizon totals down to exact X if store horizon > X
        p50_x = _scale_forecast(p50_full, stored_h, x_days)
        p90_x = _scale_forecast(p90_full, stored_h, x_days)

        # Demand during lead time only (for stock-at-arrival)
        p50_lead = _scale_forecast(p50_full, stored_h, max(lead, 1)) if lead > 0 else 0.0
        projected_at_arrival = max(0.0, available - p50_lead)

        # Step 5a — expiration window check (before sizing)
        notes: list[str] = []
        effective_days = float(x_days)
        expiry_capped = False
        if exp_days_f is not None and exp_days_f < effective_days:
            effective_days = max(exp_days_f, 0.0)
            expiry_capped = True
            notes.append(
                f"Coverage capped to {effective_days:g}d by expiration "
                f"(requested {x_days}d)."
            )

        p90_eff = _scale_forecast(p90_full, stored_h, int(max(round(effective_days), 1)))
        if effective_days <= 0:
            p90_eff = 0.0

        projected_required = p90_eff  # Decision 2 — order to P90

        # Step 4 — items to order
        raw_need = max(0.0, projected_required - available)

        # Step 5b — whole-box round
        qty_box, did_round = _round_up_to_box(raw_need, box_qty)
        qty_before_recheck = qty_box

        # Step 5c — re-check expiry after rounding (don't overshoot sellable window)
        if expiry_capped and qty_box > 0 and effective_days > 0:
            # Max units sellable in expiry window ≈ P90 for that window
            max_sellable = p90_eff
            if qty_box > max_sellable + available:
                # Cap order so available + order ≈ max sellable in window
                capped = max(0.0, max_sellable - available)
                qty_box, _ = _round_up_to_box(capped, box_qty)
                # If rounding pushes past again, keep floor boxes that fit
                while qty_box > 0 and (available + qty_box) > max_sellable + 1e-6:
                    qty_box = max(0.0, qty_box - box_qty)
                notes.append(
                    f"After box round, re-capped for expiration "
                    f"({qty_before_recheck:g} → {qty_box:g})."
                )

        item = DetectOrderItem(
            item_id=item_id,
            upc=it.get("upc"),
            sku=it.get("sku"),
            description=str(it.get("description") or ""),
            vendor_id=vendor_id,
            demand_class=str(demand_class) if demand_class else None,
            available_stock=available,
            last_pallet_qty=float(last_pallet) if last_pallet is not None else None,
            expiration_days_remaining=exp_days_f,
            box_qty=box_qty,
            horizon_days=x_days,
            forecast_horizon_used=stored_h,
            p50_demand=round(p50_x, 2),
            p90_demand=round(p90_x, 2),
            projected_stock_required=round(projected_required, 2),
            projected_stock_at_arrival=round(projected_at_arrival, 2),
            raw_qty_to_order=round(raw_need, 2),
            qty_before_box_round=round(raw_need, 2),
            qty_to_order=round(qty_box, 2),
            expiry_capped=expiry_capped,
            expiry_cap_days=effective_days if expiry_capped else None,
            box_rounded=did_round,
            validation_notes=notes,
        )
        item.justification = _gpt_or_template_justification(
            item, lead=lead, cover=cover, enabled=req.generate_justification
        )
        lines.append(item)

    order_lines = [x for x in lines if x.qty_to_order > 0]
    out_items = lines if req.include_zero_orders else order_lines

    run_id = new_run_id()
    response = DetectOrderResponse(
        ok=True,
        run_id=run_id,
        vendors=vendors,
        vendor=vendor_info,
        lead_time_days=lead,
        time_to_cover_days=cover,
        x_days=x_days,
        item_count=len(lines),
        order_line_count=len(order_lines),
        total_units_to_order=round(sum(x.qty_to_order for x in order_lines), 2),
        items=out_items,
        db_mode=repo.mode,  # type: ignore[arg-type]
        forecast_mode=store.mode,  # type: ignore[arg-type]
        message=(
            f"W-1 detect-order for {vendor_name}: X={x_days}d "
            f"(L={lead}+C={cover}). {len(order_lines)} lines to order. run_id={run_id}"
        ),
    )

    # Decision 8 — persist run for chatbot scoping
    save_order_run(response.model_dump())
    return response


def get_order_run(run_id: str) -> dict[str, Any] | None:
    from api.services.order_run_store import load_order_run

    return load_order_run(run_id)
