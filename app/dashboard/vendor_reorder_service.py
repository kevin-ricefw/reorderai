"""Vendor-grouped reorder for tracked POS vendors + no-schedule vendor planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from app.dashboard.catalog_gap_suggestions import build_catalog_gap_suggestions
from app.dashboard.past_invoice_patterns import (
    apply_invoice_order_cap,
    build_invoice_order_patterns,
    lookup_invoice_pattern,
)
from app.dashboard.pos_data_service import build_enriched_sales_cached, load_inventory
from app.dashboard.pos_reorder_math import compute_pos_ai_min
from app.dashboard.similar_products import (
    attach_same_item_stock_from_inventory,
    build_similar_product_groups,
)
from app.dashboard.vendor_catalog_loader import (
    DEFAULT_NO_SCHEDULE_COVER_DAYS,
    get_all_store_vendors,
    get_vendor_meta_by_key,
    get_vendor_meta_for_inventory_name,
    load_all_tracked_vendor_info,
    load_delivery_schedule,
    load_vendor_catalog,
    match_catalog_to_inventory,
    resolve_planning_cover_days,
    tracked_inventory_vendor_names,
)
from v2.analytics.produce_filter import filter_dataframe_excluding_produce
from v2.analytics.syntetos_boylan import CLASS_INTERMITTENT, CLASS_LUMPY, CLASS_SINGLE_HIT
from v2.forecasting.calendar_uplift import (
    COVER_DAY_OPTIONS,
    adjusted_reorder_qty,
    explain_sku_uplift_for_cover_window,
    explain_uplift_for_cover_window,
    forecast_demand_context,
    uplift_for_cover_window,
)
from v2.inventory_math.pack_size import pack_units_ordered, round_up_to_pack
from v2.signals.regional_news import load_cached_signals, match_product_news_factor

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORECASTS_PATH = PROJECT_ROOT / "outputs" / "analytics" / "sku_demand_forecasts.csv"


def _build_sales_index(enriched_sales: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """UPC -> daily quantity series (built once per plan — avoids O(n²) filtering)."""
    if enriched_sales.empty or "upc" not in enriched_sales.columns:
        return {}
    df = enriched_sales.copy()
    df["upc"] = df["upc"].astype(str).str.strip()
    empty = pd.DataFrame(columns=["date", "quantity"])
    index: dict[str, pd.DataFrame] = {}
    for upc, grp in df.groupby("upc", sort=False):
        daily = grp.groupby("date", as_index=False)["quantity"].sum().sort_values("date")
        index[upc] = daily.reset_index(drop=True) if not daily.empty else empty.copy()
    return index


def _filter_inventory_active(
    inventory: pd.DataFrame,
    *,
    active_only: bool,
    ads_window_days: int = 30,
) -> pd.DataFrame:
    """Optionally hide POS-inactive flags. Default is show everything — team decides."""
    del ads_window_days  # lookback is applied in ADS math, not for dropping SKUs
    if not active_only or "active" not in inventory.columns or inventory.empty:
        return inventory
    return inventory[inventory["active"]].copy()


def _daily_sales_for_upc(
    enriched_sales: pd.DataFrame,
    upc: str,
    sales_index: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    if sales_index is not None:
        return sales_index.get(str(upc).strip(), pd.DataFrame(columns=["date", "quantity"]))
    sub = enriched_sales[enriched_sales["upc"] == upc]
    if sub.empty:
        return pd.DataFrame(columns=["date", "quantity"])
    return (
        sub.groupby("date", as_index=False)["quantity"]
        .sum()
        .sort_values("date")
        .reset_index(drop=True)
    )


def _vendor_meta_for_inventory_name(vendor_name: str) -> dict[str, Any]:
    return get_vendor_meta_for_inventory_name(vendor_name)


def _upc_lookup_keys(upc: str) -> list[str]:
    """Match inventory UPCs (zero-padded) to forecast keys (sometimes unpadded)."""
    raw = str(upc or "").strip().replace(".0", "")
    if not raw or raw.lower() in {"nan", "none"}:
        return []
    keys = [raw]
    stripped = raw.lstrip("0") or "0"
    if stripped != raw:
        keys.append(stripped)
    if stripped.isdigit():
        keys.append(stripped.zfill(13))
        keys.append(stripped.zfill(12))
    # preserve order, drop dupes
    return list(dict.fromkeys(keys))


def _load_ml_forecasts() -> dict[str, dict[str, float]]:
    """UPC -> {forecast_7d, forecast_14d, forecast_30d} from offline SKU analysis."""
    if not FORECASTS_PATH.exists():
        return {}
    df = pd.read_csv(FORECASTS_PATH, dtype={"upc": str})
    if df.empty or "upc" not in df.columns:
        return {}
    df["upc"] = df["upc"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    out: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        payload = {
            "forecast_7d": float(row.get("forecast_7d") or 0),
            "forecast_14d": float(row.get("forecast_14d") or 0),
            "forecast_30d": float(row.get("forecast_30d") or 0),
        }
        for key in _upc_lookup_keys(row["upc"]):
            out[key] = payload
    return out


def _get_ml_row(ml_forecasts: dict[str, dict[str, float]] | None, upc: str) -> dict[str, float] | None:
    if not ml_forecasts:
        return None
    for key in _upc_lookup_keys(upc):
        row = ml_forecasts.get(key)
        if row is not None:
            return row
    return None


def _forecast_for_cover(ml_row: dict[str, float] | None, cover_days: int) -> float:
    """Map horizon days onto offline 7d / 14d / 30d ML forecasts (interpolate in between).

    Window 1: horizon = Lead Time + Days to Cover.
    """
    if not ml_row:
        return 0.0
    d = max(int(cover_days), 1)
    f7 = float(ml_row.get("forecast_7d") or 0.0)
    f14 = float(ml_row.get("forecast_14d") or 0.0)
    f30 = float(ml_row.get("forecast_30d") or 0.0)
    if d <= 7:
        return f7 if d == 7 else f7 * (d / 7.0)
    if d <= 14:
        return f7 + (f14 - f7) * ((d - 7) / 7.0)
    if d >= 30:
        # Scale beyond 30d using 30d rate
        return f30 * (d / 30.0) if d > 30 else f30
    # 15..29: interpolate 14d → 30d
    return f14 + (f30 - f14) * ((d - 14) / 16.0)


def _load_sbc_lookup() -> dict[str, dict[str, Any]]:
    """UPC -> Syntetos-Boylan stats from latest analytics CSV (or empty)."""
    path = PROJECT_ROOT / "outputs" / "analytics" / "syntetos_boylan_demand_patterns.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype={"upc": str})
    except Exception:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for _, row in df.iterrows():
        upc = str(row.get("upc") or "").strip()
        if not upc:
            continue
        out[upc] = {
            "demand_class": row.get("demand_class"),
            "adi": float(row["adi"]) if pd.notna(row.get("adi")) else None,
            "cv2": float(row["cv2"]) if pd.notna(row.get("cv2")) else None,
        }
        # unpadded alias
        out[upc.lstrip("0") or upc] = out[upc]
    return out


# Side-by-side forecast / order compare for the Vendor Reorder Planner UI
FORECAST_COMPARE_DAYS: tuple[int, ...] = (7, 14, 25)


def _resolve_pack_size(
    pack_raw: Any,
    invoice_pattern: dict[str, Any] | None,
) -> tuple[int, str]:
    """
    Units in 1 case.

    Prefer inventory pack when known (>1); else typical case count from past invoices.
    """
    if pack_raw is not None and not (isinstance(pack_raw, float) and pd.isna(pack_raw)):
        try:
            p = int(float(pack_raw))
            if p > 1:
                return p, "inventory"
        except (TypeError, ValueError):
            pass
    if invoice_pattern:
        tc = invoice_pattern.get("typical_case_count")
        if tc is not None and not (isinstance(tc, float) and pd.isna(tc)):
            try:
                p = int(round(float(tc)))
                if p > 1:
                    return p, "invoice"
            except (TypeError, ValueError):
                pass
    return 1, "unknown"


def _sales_as_of_date(enriched_sales: pd.DataFrame) -> pd.Timestamp | None:
    """Latest sale date across the store — anchors ADS lookback (includes recent zero days)."""
    if enriched_sales is None or enriched_sales.empty or "date" not in enriched_sales.columns:
        return None
    ref = pd.to_datetime(enriched_sales["date"], errors="coerce").max()
    if pd.isna(ref):
        return None
    return pd.Timestamp(ref).normalize()


def compute_product_reorder(
    inv_row: pd.Series,
    enriched_sales: pd.DataFrame,
    schedule: pd.DataFrame,
    *,
    ads_window_days: int = 30,
    horizon_uplift: float = 1.0,
    cover_days: int | None = None,
    force_cover_for_unscheduled: bool = False,
    ml_forecasts: dict[str, dict[str, float]] | None = None,
    use_ml_forecast: bool = False,
    sales_index: dict[str, pd.DataFrame] | None = None,
    uplift_explanation: dict[str, Any] | None = None,
    as_of_date: pd.Timestamp | str | None = None,
    use_future_uplift: bool = False,
    days_to_cover: int | None = None,
    vendor_lead_override: int | None = None,
    strategy_mode: str = "hybrid",
    use_news_signals: bool = True,
    sbc_lookup: dict[str, dict[str, Any]] | None = None,
    news_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Reorder math for one product: formula + Hybrid ML + uplift/news."""
    upc = str(inv_row.get("upc", "")).strip()
    vendor = str(inv_row.get("vendor_name", "Unknown")).strip()
    pack_raw = inv_row.get("pack")
    stock_raw = float(inv_row.get("QuantityOnHand") or 0)
    # −45 means 45 units sold (count wrong / receive not added). Use that in ADS.
    # For on-hand need math, treat negative as 0 (we may still hold some physical stock).
    negative_sold = max(0.0, -stock_raw)
    stock = max(stock_raw, 0.0)
    dept = str(inv_row.get("dept_name", "") or "")
    description = str(inv_row.get("description", "") or "")

    mode = str(strategy_mode or "hybrid").strip().lower()
    if mode not in {"formula", "hybrid"}:
        mode = "hybrid"
    use_ml = bool(use_ml_forecast) and mode != "formula"

    vmeta = _vendor_meta_for_inventory_name(vendor)

    # --- Planning horizon (Window 1: Lead Time + Days to Cover) ---
    if days_to_cover is not None or vendor_lead_override is not None:
        _, sched = resolve_planning_cover_days(vendor, schedule)
        if vendor_lead_override is not None:
            vendor_lead = max(1, int(vendor_lead_override))
        else:
            vendor_lead = max(
                1, int(sched.get("lead_time_days") or DEFAULT_NO_SCHEDULE_COVER_DAYS)
            )
        cover_part = max(0, int(days_to_cover)) if days_to_cover is not None else 0
        planning_horizon = max(1, vendor_lead + cover_part)
        schedule_known = bool(sched.get("has_known_schedule", False))
    else:
        explicit_cover = None
        if cover_days is not None:
            _, sched_probe = resolve_planning_cover_days(vendor, schedule)
            if force_cover_for_unscheduled:
                if not sched_probe.get("has_known_schedule"):
                    explicit_cover = cover_days
            else:
                explicit_cover = cover_days

        lead, sched = resolve_planning_cover_days(
            vendor,
            schedule,
            explicit_cover_days=explicit_cover,
        )
        vendor_lead = int(sched.get("lead_time_days", lead))
        schedule_known = bool(sched.get("has_known_schedule", False))
        planning_horizon = max(1, int(lead))
        cover_part = max(0, planning_horizon - max(0, vendor_lead))

    daily = _daily_sales_for_upc(enriched_sales, upc, sales_index)
    as_of = as_of_date if as_of_date is not None else _sales_as_of_date(enriched_sales)
    calc = compute_pos_ai_min(
        daily,
        planning_horizon,
        ads_window_days=ads_window_days,
        extra_sold_units=negative_sold,
        as_of_date=as_of,
    )

    ai_min = int(calc["ai_min"])
    ads = float(calc["ads"])
    safety = int(calc["safety_stock"])

    raw_need = max(0, ai_min - stock)

    # --- Syntetos-Boylan class (softens ML for intermittent/lumpy/single-hit) ---
    demand_class = None
    adi = None
    cv2 = None
    if sbc_lookup:
        sbc_row = None
        for key in _upc_lookup_keys(upc):
            if key in sbc_lookup:
                sbc_row = sbc_lookup[key]
                break
        if sbc_row is None:
            bare = upc.lstrip("0") or upc
            sbc_row = sbc_lookup.get(upc) or sbc_lookup.get(bare)
        if sbc_row:
            demand_class = sbc_row.get("demand_class")
            adi = sbc_row.get("adi")
            cv2 = sbc_row.get("cv2")

    ml_forecast_demand = 0.0
    ml_need = 0
    if use_ml_forecast and ml_forecasts:
        ml_row = _get_ml_row(ml_forecasts, upc)
        ml_forecast_demand = _forecast_for_cover(ml_row, planning_horizon)
        if ml_forecast_demand > 0:
            ml_need = max(0, int(round(ml_forecast_demand - stock)))

    ml_need_eff = int(ml_need)
    if demand_class in (CLASS_INTERMITTENT, CLASS_LUMPY, CLASS_SINGLE_HIT):
        ml_need_eff = int(round(ml_need * 0.5))

    formula_combined = int(round(raw_need))
    hybrid_combined = max(formula_combined, ml_need_eff)
    if mode == "formula" or not use_ml:
        combined_need = formula_combined
    else:
        combined_need = hybrid_combined

    # Per-item uplift from THIS SKU's own weekend/festival sales pattern (not store-wide).
    sku_uplift_expl = uplift_explanation or {
        "uplift_factor": 1.0,
        "summary": "Calendar uplift off.",
        "formula": "uplifted_need = need × 1.0",
    }
    applied_uplift = 1.0
    if use_future_uplift:
        from_date = None
        if as_of is not None and not pd.isna(as_of):
            from datetime import timedelta as _td

            from_date = pd.Timestamp(as_of).normalize().date() + _td(days=1)
        sku_uplift_expl = explain_sku_uplift_for_cover_window(
            daily,
            int(planning_horizon),
            from_date=from_date,
        )
        applied_uplift = float(sku_uplift_expl.get("uplift_factor") or 1.0)
    elif horizon_uplift is not None and abs(float(horizon_uplift) - 1.0) > 1e-9:
        # Legacy / test path: explicit non-1.0 factor
        applied_uplift = float(horizon_uplift)
        sku_uplift_expl = uplift_explanation or {
            "uplift_factor": applied_uplift,
            "summary": f"Explicit uplift factor {applied_uplift:.3f}.",
            "formula": f"uplifted_need = need × {applied_uplift:.3f}",
        }

    # Apply uplift ONLY to expected sales — not safety stock.
    lead_time_demand = int(calc.get("lead_time_demand") or 0)
    expected_need = max(0.0, float(lead_time_demand) - float(stock))

    def _uplift_need(base_need: int) -> tuple[int, int]:
        safety_need = max(0.0, float(base_need) - expected_need)
        if applied_uplift > 1.0 and expected_need > 0:
            uplifted = int(round(expected_need * applied_uplift + safety_need))
        else:
            uplifted, _ = adjusted_reorder_qty(base_need, applied_uplift, 1)
        uplifted = max(int(base_need), int(uplifted))
        return uplifted, max(0, int(round(uplifted - base_need)))

    uplifted_need, uplift_extra_units = _uplift_need(int(combined_need))
    formula_uplifted, _ = _uplift_need(formula_combined)
    hybrid_uplifted, _ = _uplift_need(hybrid_combined)

    # Regional news (Okemos / Mid-Michigan): multiply uplifted need.
    news_factor = 1.0
    news_signal = ""
    if use_news_signals:
        news = match_product_news_factor(
            description=description,
            department=dept,
            signals=news_signals,
        )
        news_factor = float(news.get("news_factor") or 1.0)
        news_signal = str(news.get("news_signal") or "")
        if abs(news_factor - 1.0) > 1e-9:
            uplifted_need = max(0, int(round(uplifted_need * news_factor)))
            formula_uplifted = max(0, int(round(formula_uplifted * news_factor)))
            hybrid_uplifted = max(0, int(round(hybrid_uplifted * news_factor)))

    # Shop-size / shelf + case size from past invoices
    invoice_cap_note = ""
    invoice_pattern = None
    try:
        patterns = build_invoice_order_patterns()
        invoice_pattern = lookup_invoice_pattern(
            patterns,
            upc=upc,
            description=description,
            pos_name=description,
        )
    except Exception:
        invoice_pattern = None

    pack_int, pack_source = _resolve_pack_size(pack_raw, invoice_pattern)

    def _cap_and_pack(need_units: int) -> tuple[int, int, str]:
        need_for_pack = int(max(round(need_units), 0))
        note = ""
        if invoice_pattern is not None:
            need_for_pack, note = apply_invoice_order_cap(
                need_for_pack,
                invoice_pattern,
                min_qty=int(round(expected_need)),
                pack_size=pack_int,
            )
        qty = round_up_to_pack(need_for_pack, pack_int)
        return need_for_pack, qty, note

    need_for_pack, order_qty, invoice_cap_note = _cap_and_pack(uplifted_need)
    ai_need_units = int(max(round(uplifted_need), 0))
    _, formula_order_qty, _ = _cap_and_pack(formula_uplifted)
    _, hybrid_order_qty, _ = _cap_and_pack(hybrid_uplifted)

    cases_to_order = pack_units_ordered(order_qty, pack_int)
    reorder_needed = order_qty > 0
    suggested_qty = int(order_qty)
    extra_units = max(0, int(order_qty) - int(need_for_pack)) if pack_int > 1 else 0
    extra_holding_days = round(extra_units / ads, 1) if ads > 0 and extra_units > 0 else 0.0

    # Confidence by Syntetos-Boylan demand class
    _confidence_map = {
        "Smooth": 0.90,
        "Erratic": 0.70,
        "Intermittent": 0.55,
        "Lumpy": 0.40,
        CLASS_INTERMITTENT: 0.55,
        CLASS_LUMPY: 0.40,
        CLASS_SINGLE_HIT: 0.30,
        "Single demand day": 0.30,
        "No demand": 0.20,
    }
    confidence_score = float(_confidence_map.get(str(demand_class or ""), 0.50))

    # Human-readable need math: Expected + Safety [+ Uplift] [- On hand] = Total
    exp_i = int(lead_time_demand)
    saf_i = int(safety)
    up_i = int(uplift_extra_units)
    stk_i = int(round(stock))
    need_parts = [str(exp_i), str(saf_i)]
    if up_i > 0:
        need_parts.append(str(up_i))
    need_calc = "+".join(need_parts)
    if stk_i > 0:
        need_calc = f"{need_calc}-{stk_i}"
    need_calc = f"{need_calc}={int(ai_need_units)}"

    if pack_int > 1:
        pack_note = (
            f"Case = {pack_int} units ({pack_source}). "
            f"Need {need_for_pack} -> {order_qty} units "
            f"({cases_to_order:g} case(s)); next case only if leftover >=50% of case."
        )
    else:
        pack_note = "Case size unknown — order in units (no pack rounding)."
    if invoice_cap_note:
        pack_note = f"{invoice_cap_note} | {pack_note}"

    reorder_reason = "formula"
    if use_ml and ml_need_eff > raw_need:
        reorder_reason = "ml_forecast"
    elif applied_uplift > 1.0 and uplifted_need > combined_need:
        reorder_reason = "calendar_uplift"
    elif abs(news_factor - 1.0) > 1e-9 and news_signal:
        reorder_reason = "news_signal"

    expl = sku_uplift_expl or {}
    driver_bits = []
    if expl.get("driver_day_name"):
        driver_bits.append(str(expl["driver_day_name"]))
    if expl.get("driver_day_type"):
        driver_bits.append(str(expl["driver_day_type"]))
    if expl.get("driver_festival"):
        driver_bits.append(f"festival {expl['driver_festival']}")
    if expl.get("driver_holiday"):
        driver_bits.append(f"holiday {expl['driver_holiday']}")
    if expl.get("driver_long_weekend"):
        driver_bits.append("long weekend")
    driver_txt = ", ".join(driver_bits) if driver_bits else "baseline"
    if applied_uplift > 1.0 and combined_need > 0:
        uplift_note = (
            f"x {applied_uplift:.3f} on expected sales only "
            f"({expl.get('driver_date', '?')}: {driver_txt}) — soft past pattern, not a guaranteed extra case"
        )
        order_math_note = (
            f"horizon {planning_horizon}d (lead {vendor_lead}+cover {cover_part}); "
            f"AI min {ai_min}; formula need {int(round(raw_need))}; ML need {ml_need_eff} "
            f"-> need {int(round(combined_need))}; uplift {applied_uplift:.3f} on expected "
            f"{int(round(expected_need))} only (+{uplift_extra_units}); news ×{news_factor:.3f} "
            f"-> pack need {need_for_pack} -> order {order_qty} units "
            f"({cases_to_order:g} case(s) × {pack_int}); mode={mode}"
        )
    else:
        uplift_note = "x 1.000 (this item has no weekend/festival boost in its own sales)"
        order_math_note = (
            f"horizon {planning_horizon}d (lead {vendor_lead}+cover {cover_part}); "
            f"AI min {ai_min}; formula need {int(round(raw_need))}; ML need {ml_need_eff} "
            f"-> need {int(round(combined_need))}; news ×{news_factor:.3f} "
            f"-> pack need {need_for_pack} -> order {order_qty} units "
            f"({cases_to_order:g} case(s) × {pack_int}); mode={mode}"
        )

    return {
        "upc": upc,
        "description": description,
        "vendor_name": vendor,
        "vendor_key": vmeta.get("key", ""),
        "department": dept,
        "pack_size": pack_int,
        "pack_source": pack_source,
        "current_stock": round(stock_raw, 2),  # POS count as-is (may be negative)
        "stock_for_reorder": round(stock, 2),  # negatives treated as 0 on-hand
        "negative_sold_units": int(round(negative_sold)),  # |neg| counted into ADS
        "ai_min": ai_min,
        "safety_stock": safety,
        "demand_std": float(calc.get("demand_std") or 0.0),
        "lead_time_demand": int(calc.get("lead_time_demand") or 0),
        "ads": ads,
        "lookback_days": int(ads_window_days),
        "sold_in_lookback": int(calc.get("total_sold_30d") or 0),
        "total_sold_30d": calc["total_sold_30d"],
        "lead_time_days": vendor_lead,
        "days_to_cover": int(cover_part),
        "forecast_horizon_days": int(planning_horizon),
        "planning_cover_days": int(planning_horizon),
        "schedule_known": schedule_known,
        "order_cutoff": sched.get("order_cutoff", ""),
        "delivery_days": sched.get("delivery_days", ""),
        "delivery_day_labels": sched.get("delivery_day_labels", ""),
        "order_frequency": sched.get("order_frequency", ""),
        "min_days_cover": sched.get("min_days_cover", ""),
        "formula_breakdown": calc["formula"],
        "units_needed": int(round(combined_need)),
        "ai_need_units": int(ai_need_units),
        "need_calc": need_calc,
        "units_after_invoice_cap": int(need_for_pack),
        "formula_raw_need": int(round(raw_need)),
        "ml_forecast_demand": round(ml_forecast_demand, 2),
        "forecast_demand": round(ml_forecast_demand, 2),
        "ml_need": ml_need_eff,
        "horizon_uplift": round(applied_uplift, 3),
        "uplift_note": uplift_note,
        "order_math_note": order_math_note,
        "units_after_uplift": uplifted_need,
        "uplift_extra_units": uplift_extra_units,
        "uplift_summary": str(expl.get("summary") or ""),
        "order_qty": order_qty,
        "suggested_qty": suggested_qty,
        "formula_order_qty": int(formula_order_qty),
        "hybrid_order_qty": int(hybrid_order_qty),
        "strategy_mode": mode,
        "demand_class": demand_class,
        "adi": adi,
        "cv2": cv2,
        "news_factor": round(news_factor, 3),
        "news_signal": news_signal,
        "confidence_score": round(confidence_score, 3),
        "extra_units": int(extra_units),
        "extra_holding_days": extra_holding_days,
        "invoice_cap_note": invoice_cap_note,
        "invoice_max_cases": float(invoice_pattern.get("max_cases") or 0) if invoice_pattern else 0.0,
        "invoice_max_units": (
            float(
                max(
                    float(invoice_pattern.get("max_units") or 0),
                    float(invoice_pattern.get("max_cases") or 0) * float(pack_int),
                )
            )
            if invoice_pattern
            else 0.0
        ),
        "invoice_median_units": float(invoice_pattern.get("median_units") or 0) if invoice_pattern else 0.0,
        "invoice_order_count": int(invoice_pattern.get("order_count") or 0) if invoice_pattern else 0,
        "cases_to_order": float(cases_to_order),
        "pack_rounding_note": pack_note,
        "reorder_needed": reorder_needed,
        "reorder_reason": reorder_reason,
        "catalog_matched": bool(inv_row.get("catalog_matched", False)),
        "catalog_match_score": float(inv_row.get("catalog_match_score") or 0),
        "catalog_product_name": str(inv_row.get("catalog_product_name", "") or ""),
        "case_cost": float(inv_row.get("case_cost") or 0) if pd.notna(inv_row.get("case_cost")) else None,
        "est_order_cost": round((float(inv_row.get("case_cost") or 0) or 0) * pack_units_ordered(order_qty, pack_int), 2)
        if order_qty > 0 and pd.notna(inv_row.get("case_cost"))
        else None,
    }
def _merge_catalog_into_inventory(store_inventory: pd.DataFrame) -> pd.DataFrame:
    """
    Match each vendor Excel catalog to inventory.

    Order-form vendors pull matching SKUs even when POS vendor_name differs
    (Anand under BABCO → Annapurna). Later vendors skip already-claimed UPCs
    so the same item is not ordered twice.
    """
    frames: list[pd.DataFrame] = []
    claimed: set[str] = set()

    # Legacy / order-form vendors first so they claim brand SKUs before dumps like BABCO
    vendors = sorted(
        get_all_store_vendors(),
        key=lambda v: (0 if v.get("legacy") else 1, v["inventory_names"][0]),
    )

    for v in vendors:
        vnames = v["inventory_names"]
        subset = store_inventory[store_inventory["vendor_name"].isin(vnames)]
        has_catalog = bool(v.get("catalog_file") or v.get("legacy"))
        if has_catalog:
            try:
                cat = load_vendor_catalog(v["key"])
            except Exception:
                cat = pd.DataFrame()
            if not cat.empty:
                matched = match_catalog_to_inventory(
                    cat,
                    store_inventory,
                    vnames,
                    include_other_vendors=True,
                )
                if claimed:
                    matched = matched[~matched["upc"].astype(str).isin(claimed)]
                if matched.empty:
                    continue
                claimed.update(matched["upc"].astype(str).tolist())
                frames.append(matched)
                continue

        if subset.empty:
            continue
        s = subset[~subset["upc"].astype(str).isin(claimed)].copy()
        if s.empty:
            continue
        s["catalog_matched"] = True
        s["catalog_match_score"] = 100.0
        s["catalog_product_name"] = s.get("description", "")
        claimed.update(s["upc"].astype(str).tolist())
        frames.append(s)

    if not frames:
        return store_inventory
    return pd.concat(frames, ignore_index=True).drop_duplicates(subset=["upc"])

def build_cover_comparison(
    inventory: pd.DataFrame,
    enriched_sales: pd.DataFrame,
    *,
    cover_options: tuple[int, ...] = COVER_DAY_OPTIONS,
    ads_window_days: int = 30,
    unscheduled_only: bool = True,
    ml_forecasts: dict[str, dict[str, float]] | None = None,
    use_ml_forecast: bool = False,
) -> pd.DataFrame:
    """
    Side-by-side AI min / need / order qty (and optional ML forecast) per cover horizon.
    """
    schedule = load_delivery_schedule()
    sales_index = _build_sales_index(enriched_sales)
    as_of = _sales_as_of_date(enriched_sales)
    rows = []
    for _, row in inventory.iterrows():
        vendor = str(row.get("vendor_name", ""))
        _, sched = resolve_planning_cover_days(vendor, schedule)
        if unscheduled_only and sched.get("has_known_schedule"):
            continue

        upc = str(row.get("upc", "")).strip()
        stock_raw = float(row.get("QuantityOnHand") or 0)
        negative_sold = max(0.0, -stock_raw)
        stock = max(stock_raw, 0.0)
        try:
            inv_pat = lookup_invoice_pattern(
                build_invoice_order_patterns(),
                upc=upc,
                description=str(row.get("description", "")),
                pos_name=str(row.get("description", "")),
            )
        except Exception:
            inv_pat = None
        pack_int, _pack_src = _resolve_pack_size(row.get("pack"), inv_pat)
        daily = _daily_sales_for_upc(enriched_sales, upc, sales_index)
        ml_row = _get_ml_row(ml_forecasts, upc) if use_ml_forecast else None

        entry: dict[str, Any] = {
            "upc": upc,
            "vendor_name": vendor,
            "description": str(row.get("description", "")),
            "current_stock": round(stock_raw, 2),
            "pack_size": pack_int,
            "ads": 0.0,
            "schedule_known": bool(sched.get("has_known_schedule")),
        }
        for days in cover_options:
            calc = compute_pos_ai_min(
                daily,
                days,
                ads_window_days=ads_window_days,
                extra_sold_units=negative_sold,
                as_of_date=as_of,
            )
            ai_min = int(calc["ai_min"])
            formula_need = max(0, ai_min - stock)
            ml_fc = round(_forecast_for_cover(ml_row, days), 1) if ml_row else 0.0
            ml_need = max(0, int(round(ml_fc - stock))) if ml_fc > 0 else 0
            need = max(formula_need, ml_need)
            order_qty = round_up_to_pack(need, pack_int)
            entry[f"ai_min_{days}d"] = ai_min
            entry[f"ml_fc_{days}d"] = ml_fc
            entry[f"need_{days}d"] = need
            entry[f"order_{days}d"] = order_qty
            entry[f"cases_{days}d"] = pack_units_ordered(order_qty, pack_int)
            if entry["ads"] == 0:
                entry["ads"] = round(float(calc["ads"]), 2)
        rows.append(entry)

    return pd.DataFrame(rows)


def _merge_single_vendor_catalog(
    store_inventory: pd.DataFrame,
    vmeta: dict[str, Any],
    *,
    full_inventory: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Match vendor catalog to POS inventory; pull in catalog SKUs even if POS vendor differs."""
    vnames = vmeta["inventory_names"]
    search_inv = full_inventory if full_inventory is not None else store_inventory
    subset = store_inventory[store_inventory["vendor_name"].isin(vnames)]
    # Always fuzzy-match real vendor catalogs (HOS Case Qty, Premier, Annapurna form, etc.)
    if vmeta.get("legacy") or vmeta.get("catalog_file"):
        try:
            cat = load_vendor_catalog(vmeta["key"])
            if not cat.empty:
                matched = match_catalog_to_inventory(
                    cat,
                    search_inv,
                    vnames,
                    include_other_vendors=True,
                )
                # Manual pack_overrides.csv always wins over catalog guesses
                from app.dashboard.pos_data_service import _apply_pack_overrides

                return _apply_pack_overrides(matched)
        except Exception:
            pass
    if subset.empty:
        return subset
    s = subset.copy()
    s["catalog_matched"] = True
    s["catalog_match_score"] = 100.0
    s["catalog_product_name"] = s.get("description", "")
    return s


def build_vendor_order_view(
    vendor_key: str,
    *,
    cover_days: int = DEFAULT_NO_SCHEDULE_COVER_DAYS,
    ads_window_days: int = 30,
    active_only: bool = False,
    use_future_uplift: bool = True,
    use_ml_forecast: bool = True,
    days_to_cover: int | None = None,
    vendor_lead_days: int | None = None,
    strategy_mode: str = "hybrid",
    use_news_signals: bool = True,
) -> dict[str, Any]:
    """
    Reorder plan for a single vendor (Window 1).

    Forecast horizon = Vendor Lead Time + Days to Cover when both provided.
    Always Hybrid ML: max(formula need, horizon forecast − stock).
    """
    vmeta = get_vendor_meta_by_key(vendor_key)
    if not vmeta:
        return {"error": f"Unknown vendor: {vendor_key}"}

    vendor_name = vmeta["inventory_names"][0]
    inventory = load_inventory()
    inventory = _filter_inventory_active(
        inventory, active_only=active_only, ads_window_days=ads_window_days
    )

    vnames = vmeta["inventory_names"]
    vendor_inv = inventory[inventory["vendor_name"].isin(vnames)].copy()
    if vendor_inv.empty:
        norm = {n.upper().strip() for n in vnames}
        inv_norm = inventory.copy()
        inv_norm["_vn"] = inv_norm["vendor_name"].astype(str).str.upper().str.strip()
        vendor_inv = inv_norm[inv_norm["_vn"].isin(norm)].drop(columns=["_vn"], errors="ignore")
    vendor_inv = _merge_single_vendor_catalog(vendor_inv, vmeta, full_inventory=inventory)
    vendor_inv = filter_dataframe_excluding_produce(
        vendor_inv,
        name_col="description",
        dept_col="dept_name",
        upc_col="upc",
    )

    enriched = build_enriched_sales_cached()
    if not enriched.empty:
        enriched = filter_dataframe_excluding_produce(
            enriched,
            name_col="description",
            upc_col="upc",
            inventory=vendor_inv,
        )
    sales_index = _build_sales_index(enriched)
    schedule = load_delivery_schedule()
    _, sched = resolve_planning_cover_days(vendor_name, schedule)
    schedule_known = bool(sched.get("has_known_schedule", False))

    mode = (strategy_mode or "hybrid").strip().lower()
    use_ml = bool(use_ml_forecast) and mode != "formula"
    ml_forecasts = _load_ml_forecasts() if use_ml else {}
    sbc_lookup = _load_sbc_lookup()
    news_payload = load_cached_signals() if use_news_signals else {"signals": []}
    news_signals = list(news_payload.get("signals") or [])

    horizon_uplift = 1.0
    uplift_explanation: dict[str, Any] = {
        "uplift_factor": 1.0,
        "summary": "Orders use per-item uplift from each SKU's own weekend/festival sales.",
        "formula": "per-item uplift (not store-wide)",
        "factors_note": (
            "Each product's uplift = that product's Sat/Sun/festival avg ÷ its weekday avg. "
            "Rice bags that sell flat stay ~1.0; snacks that spike weekends get boosted."
        ),
    }
    # Legacy planning cover when Window 1 lead/cover not passed
    planning_cover = int(sched.get("lead_time_days", cover_days)) if schedule_known else int(cover_days)
    planning_cover = int(cover_days) if cover_days else planning_cover
    if days_to_cover is not None or vendor_lead_days is not None:
        lead_part = int(vendor_lead_days) if vendor_lead_days is not None else int(
            sched.get("lead_time_days", 0) or 0
        )
        cover_part = int(days_to_cover) if days_to_cover is not None else 0
        planning_cover = max(lead_part + cover_part, 1)

    if use_future_uplift and not enriched.empty:
        store_daily = enriched.groupby("date", as_index=False)["quantity"].sum()
        ctx = forecast_demand_context(store_daily, horizon_days=30, weather_days=7)
        uplift_explanation = explain_uplift_for_cover_window(ctx, planning_cover)
        uplift_explanation["summary"] = (
            "Store outlook (info only): " + str(uplift_explanation.get("summary") or "")
            + " Orders use per-item uplift instead."
        )
        horizon_uplift = 1.0

    rows = []
    for _, inv_row in vendor_inv.iterrows():
        rows.append(
            compute_product_reorder(
                inv_row,
                enriched,
                schedule,
                ads_window_days=ads_window_days,
                horizon_uplift=1.0,
                cover_days=planning_cover,
                force_cover_for_unscheduled=False,
                ml_forecasts=ml_forecasts,
                use_ml_forecast=use_ml,
                sales_index=sales_index,
                uplift_explanation=None,
                use_future_uplift=use_future_uplift,
                days_to_cover=days_to_cover,
                vendor_lead_override=vendor_lead_days,
                strategy_mode=mode,
                use_news_signals=use_news_signals,
                sbc_lookup=sbc_lookup,
                news_signals=news_signals,
            )
        )

    products = pd.DataFrame(rows)
    # Same item, other brands — search full store inventory (name + on-hand only).
    inv_for_alts = inventory.copy()
    if "size" not in inv_for_alts.columns:
        inv_for_alts["size"] = ""
    products = attach_same_item_stock_from_inventory(products, inv_for_alts)

    # Catalog-only suggestions (1 case) — e.g. Goli flavors in Excel but not in POS yet
    try:
        cat = load_vendor_catalog(vendor_key)
        gap = build_catalog_gap_suggestions(
            cat,
            products,
            vendor_name=vendor_name,
            vendor_key=vendor_key,
        )
        if not gap.empty:
            # Align columns so concat doesn't warn on all-NA extras
            for col in products.columns:
                if col not in gap.columns:
                    gap[col] = None
            gap = gap.reindex(columns=list(products.columns) + [c for c in gap.columns if c not in products.columns])
            products = pd.concat([products, gap], ignore_index=True, sort=False)
    except Exception:
        pass

    reorder = products[products["reorder_needed"]].copy() if not products.empty else products
    if not reorder.empty:
        reorder = reorder.sort_values(["order_qty", "units_needed"], ascending=False)

    # Always build 7 / 14 / 25 forecast compare for this vendor (separate UI table).
    forecast_compare_df = pd.DataFrame()
    if not vendor_inv.empty:
        forecast_compare_df = build_cover_comparison(
            vendor_inv,
            enriched,
            cover_options=FORECAST_COMPARE_DAYS,
            ads_window_days=ads_window_days,
            unscheduled_only=False,
            ml_forecasts=ml_forecasts,
            use_ml_forecast=use_ml_forecast,
        )

    # Legacy 7/14/21/30 table for unscheduled vendors (kept for older charts).
    horizon_df = pd.DataFrame()
    if not schedule_known and not vendor_inv.empty:
        horizon_df = build_cover_comparison(
            vendor_inv,
            enriched,
            cover_options=COVER_DAY_OPTIONS,
            ads_window_days=ads_window_days,
            unscheduled_only=False,
            ml_forecasts=ml_forecasts,
            use_ml_forecast=use_ml_forecast,
        )

    start = enriched["date"].min().date() if not enriched.empty else None
    end = enriched["date"].max().date() if not enriched.empty else None

    return {
        "vendor_key": vendor_key,
        "vendor_name": vendor_name,
        "vendor_meta": vmeta,
        "schedule": sched,
        "schedule_known": schedule_known,
        "cover_days": int(planning_cover),
        "horizon_uplift": horizon_uplift,
        "uplift_explanation": uplift_explanation,
        "products": products,
        "reorder_lines": reorder,
        "forecast_compare_table": forecast_compare_df,
        "horizon_table": horizon_df,
        "sales_date_range": (start, end),
        "ml_forecast_available": bool(ml_forecasts),
    }


def build_tracked_vendor_reorder_plan(
    *,
    ads_window_days: int = 30,
    active_only: bool = False,
    use_future_uplift: bool = True,
    use_ml_forecast: bool = True,
    overall_cover_days: int = DEFAULT_NO_SCHEDULE_COVER_DAYS,
    force_cover_for_unscheduled: bool = True,
    include_all_store_vendors: bool = False,
) -> dict[str, Any]:
    """
    Reorder plan for tracked vendors + overall consolidated list.

    Vendors without delivery schedule use `overall_cover_days` (default 14).
    ML forecasts + calendar/weather uplift adjust order quantities when enabled.
    """
    inventory = load_inventory()
    inventory = _filter_inventory_active(
        inventory, active_only=active_only, ads_window_days=ads_window_days
    )

    store_names = tracked_inventory_vendor_names()
    store_inventory = inventory[inventory["vendor_name"].isin(store_names)].copy()
    store_inventory = _merge_catalog_into_inventory(store_inventory)

    store_inventory = filter_dataframe_excluding_produce(
        store_inventory,
        name_col="description",
        dept_col="dept_name",
        upc_col="upc",
    )

    similar_source = inventory.copy()
    if "size" not in similar_source.columns:
        similar_source["size"] = ""
    similar_groups = build_similar_product_groups(
        filter_dataframe_excluding_produce(
            similar_source,
            name_col="description",
            dept_col="dept_name",
            upc_col="upc",
        )
    )

    scope_inventory = store_inventory
    if include_all_store_vendors:
        extra = inventory[~inventory["vendor_name"].isin(store_names)].copy()
        if not extra.empty:
            scope_inventory = pd.concat(
                [
                    store_inventory,
                    filter_dataframe_excluding_produce(
                        extra,
                        name_col="description",
                        dept_col="dept_name",
                        upc_col="upc",
                    ),
                ],
                ignore_index=True,
            ).drop_duplicates(subset=["upc"])

    enriched = build_enriched_sales_cached()
    sales_index = _build_sales_index(enriched)
    if not enriched.empty:
        enriched = filter_dataframe_excluding_produce(
            enriched,
            name_col="description",
            upc_col="upc",
            inventory=scope_inventory,
        )
    schedule = load_delivery_schedule()
    vendor_info = load_all_tracked_vendor_info()
    ml_forecasts = _load_ml_forecasts() if use_ml_forecast else {}

    demand_context = pd.DataFrame()
    horizon_uplift = 1.0
    cover_uplift = 1.0
    uplift_expl_14: dict[str, Any] = {"uplift_factor": 1.0}
    uplift_expl_cover: dict[str, Any] = {"uplift_factor": 1.0}
    if use_future_uplift and not enriched.empty:
        store_daily = enriched.groupby("date", as_index=False)["quantity"].sum()
        demand_context = forecast_demand_context(store_daily, horizon_days=30, weather_days=7)
        uplift_expl_14 = explain_uplift_for_cover_window(demand_context, 14)
        uplift_expl_cover = explain_uplift_for_cover_window(demand_context, overall_cover_days)
        # Store outlook for Future tab only — order qty uses per-SKU uplift
        horizon_uplift = float(uplift_expl_14["uplift_factor"])
        cover_uplift = float(uplift_expl_cover["uplift_factor"])

    def _compute_row(
        row: pd.Series,
        *,
        cover: int | None = None,
    ) -> dict[str, Any]:
        return compute_product_reorder(
            row,
            enriched,
            schedule,
            ads_window_days=ads_window_days,
            horizon_uplift=1.0,
            cover_days=cover,
            force_cover_for_unscheduled=force_cover_for_unscheduled,
            ml_forecasts=ml_forecasts,
            use_ml_forecast=use_ml_forecast,
            sales_index=sales_index,
            uplift_explanation=None,
            use_future_uplift=use_future_uplift,
        )

    products = [_compute_row(row, cover=None) for _, row in store_inventory.iterrows()]
    overall_products = [
        _compute_row(row, cover=overall_cover_days) for _, row in scope_inventory.iterrows()
    ]

    product_df = pd.DataFrame(products)
    overall_df = pd.DataFrame(overall_products)
    empty_plan = {
        "products": product_df,
        "reorder_lines": product_df,
        "overall_reorder_lines": overall_df,
        "scope_inventory": scope_inventory,
        "similar_groups": similar_groups,
        "vendor_summary": pd.DataFrame(),
        "vendor_info": vendor_info,
        "enriched_sales": enriched,
        "demand_context": demand_context,
        "future_calendar": demand_context,
        "horizon_uplift": horizon_uplift,
        "cover_uplift": cover_uplift,
        "overall_cover_days": overall_cover_days,
        "force_cover_for_unscheduled": force_cover_for_unscheduled,
        "use_ml_forecast": use_ml_forecast,
        "ml_forecast_available": bool(ml_forecasts),
        "include_all_store_vendors": include_all_store_vendors,
        "sales_date_range": (None, None),
        "tracked_vendor_count": len(get_all_store_vendors()),
    }
    if product_df.empty:
        return empty_plan

    reorder_df = product_df[product_df["reorder_needed"]].copy()
    reorder_df = attach_same_item_stock_from_inventory(reorder_df, similar_source)

    overall_reorder_df = overall_df[overall_df["reorder_needed"]].copy()
    overall_reorder_df = attach_same_item_stock_from_inventory(overall_reorder_df, similar_source)
    overall_reorder_df = overall_reorder_df.sort_values(["order_qty", "units_needed"], ascending=False)

    vendor_summary = (
        reorder_df.groupby(["vendor_key", "vendor_name"], as_index=False)
        .agg(
            skus_to_order=("upc", "count"),
            total_units_needed=("units_needed", "sum"),
            total_units_to_order=("order_qty", "sum"),
            total_cases=("cases_to_order", "sum"),
            est_cost=("est_order_cost", lambda s: s.dropna().sum() if s.notna().any() else 0),
            lead_time_days=("lead_time_days", "first"),
            schedule_known=("schedule_known", "first"),
            order_cutoff=("order_cutoff", "first"),
            delivery_days=("delivery_days", "first"),
            order_frequency=("order_frequency", "first"),
        )
        .sort_values("total_units_to_order", ascending=False)
    )

    start = enriched["date"].min().date() if not enriched.empty else None
    end = enriched["date"].max().date() if not enriched.empty else None

    return {
        **empty_plan,
        "reorder_lines": reorder_df,
        "overall_reorder_lines": overall_reorder_df,
        "vendor_summary": vendor_summary,
        "sales_date_range": (start, end),
    }


def trend_by_day_type(enriched_sales: pd.DataFrame) -> pd.DataFrame:
    if enriched_sales.empty or "day_type" not in enriched_sales.columns:
        return pd.DataFrame()
    return (
        enriched_sales.groupby("day_type", as_index=False)
        .agg(total_units=("quantity", "sum"), days=("date", "nunique"), avg_daily_units=("quantity", "mean"))
        .sort_values("total_units", ascending=False)
    )


def trend_by_weather(enriched_sales: pd.DataFrame) -> pd.DataFrame:
    if enriched_sales.empty or "weather_label" not in enriched_sales.columns:
        return pd.DataFrame()
    daily = enriched_sales.groupby(["date", "weather_label"], as_index=False)["quantity"].sum()
    return (
        daily.groupby("weather_label", as_index=False)
        .agg(avg_daily_units=("quantity", "mean"), days=("date", "count"))
        .sort_values("avg_daily_units", ascending=False)
    )


def trend_by_festival(enriched_sales: pd.DataFrame) -> pd.DataFrame:
    if enriched_sales.empty:
        return pd.DataFrame()
    daily = enriched_sales.groupby("date", as_index=False).agg(
        quantity=("quantity", "sum"),
        indian_festival=("indian_festival", "first"),
        us_holiday=("us_holiday", "first"),
        is_long_weekend=("is_long_weekend", "first"),
    )
    rows = []
    fest = daily[daily["indian_festival"].notna()]
    if not fest.empty:
        rows.append({"segment": "Indian festival days", "avg_daily_units": fest["quantity"].mean(), "days": len(fest)})
    us = daily[daily["us_holiday"].notna()]
    if not us.empty:
        rows.append({"segment": "US holiday days", "avg_daily_units": us["quantity"].mean(), "days": len(us)})
    regular = daily[daily["indian_festival"].isna() & daily["us_holiday"].isna()]
    if not regular.empty:
        rows.append({"segment": "Regular days", "avg_daily_units": regular["quantity"].mean(), "days": len(regular)})
    long_wk = daily[daily["is_long_weekend"]]
    if not long_wk.empty:
        rows.append({"segment": "Long weekends", "avg_daily_units": long_wk["quantity"].mean(), "days": len(long_wk)})
    return pd.DataFrame(rows)
