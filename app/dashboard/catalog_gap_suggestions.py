"""Suggest 1-case orders for catalog products not yet in POS inventory."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pandas as pd

from app.dashboard.product_normalization import product_signature
from v2.inventory_math.pack_size import pack_units_ordered

# Always suggest these Annapurna order-form lines when POS has no matching SKU.
FORCE_ANNAPURNA_CATALOG_SUGGESTS: tuple[str, ...] = (
    "Goli Soda Lime Lemon 300MLx36",
    "Goli Soda Orange Burst 300MLx36",
    "Goli Soda Cumin Cooler 300MLx36",
    "Goli Soda Minty Mojito 300MLx36",
    "Goli Soda Rose Paneer 300MLx36",
    "Ashoka FRZ Gulab Jamun  Bucket 200PCS x 40GM",
    "Ashoka FRZ GULAB JAMUN  300 PCS",
    "Anand Frozen Malabar Parotta (Whole Wheat) 12 x 15 oz",
    "Anand Frozen Malabar Parotta (Catering Pack) 12 x 2.5 lb",
    "Anand Frozen Kothu Parotta - 12 x 1 lb",
    "Anand Frozen Ceylon Parotta 12 x 1 lb",
    "Anand Frozen Coin Parotta 12 x 1 lb",
    "Anand Frozen Garlic Parotta 12 x 1 lb",
    "MDH Kitchen King Masala 100gmx10",
)

# Brand prefixes: if any sibling is selling and this catalog line has no POS row → suggest 1 case
# Keep narrow — only soda flavors / exact families (avoid flooding with all Ashoka/Anand).
SIBLING_TREND_BRANDS: tuple[str, ...] = (
    "GOLI SODA",
)


def _norm_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(name).upper())


def _catalog_upc(name: str) -> str:
    digest = hashlib.md5(_norm_key(name).encode("utf-8")).hexdigest()[:10].upper()
    return f"CAT{digest}"


def _case_units(product_name: str, catalog_pack: Any) -> int:
    """Units in 1 case from catalog pack / name (fallback 1)."""
    pack = 0
    try:
        pack = int(float(catalog_pack)) if catalog_pack is not None and str(catalog_pack) not in {"", "nan"} else 0
    except (TypeError, ValueError):
        pack = 0
    name_u = str(product_name).upper()
    # Prefer Nx pattern like 300MLx36 or 20 x 200
    m = re.search(r"(?:X|×)\s*(\d+)\s*$", name_u.replace(" ", ""))
    if not m:
        m = re.search(r"(\d+)\s*[xX×]\s*\d+", name_u)
        if m:
            # "24 x 170" → 24; "300MLx36" handled above
            pass
    m2 = re.search(r"[xX×]\s*(\d+)\s*$", name_u) or re.search(r"(\d+)\s*[xX×]\s*\d+\s*[A-Z]*$", name_u)
    # 300MLx36 → 36
    m36 = re.search(r"[xX×](\d+)\s*$", re.sub(r"\s+", "", name_u))
    if m36:
        n = int(m36.group(1))
        if 2 <= n <= 200:
            return n
    # "12 x 1 lb" / "20 x 200 g" → leading count
    m_lead = re.search(r"(\d+)\s*[xX×]\s*", name_u)
    if m_lead:
        n = int(m_lead.group(1))
        if 2 <= n <= 200:
            return n
    if 2 <= pack <= 200:
        # Avoid treating "40GM" parse as case qty when name has PCS bucket
        if "PCS" in name_u and pack <= 50 and "X" in name_u:
            return 1
        return pack
    return 1


def _already_covered(catalog_name: str, products: pd.DataFrame) -> bool:
    if products.empty:
        return False
    key = _norm_key(catalog_name)
    for col in ("catalog_product_name", "description"):
        if col not in products.columns:
            continue
        for val in products[col].dropna().astype(str):
            vk = _norm_key(val)
            if not vk:
                continue
            if key == vk or key in vk or vk in key:
                return True
            # strong token overlap on core item
            sa = set(product_signature(catalog_name)["core_tokens"])
            sb = set(product_signature(val)["core_tokens"])
            if sa and sb and len(sa & sb) / max(len(sa | sb), 1) >= 0.8:
                return True
    return False


def _sibling_selling(catalog_name: str, products: pd.DataFrame) -> bool:
    if products.empty or "sold_in_lookback" not in products.columns:
        return False
    name_u = str(catalog_name).upper()
    brand = next((b for b in SIBLING_TREND_BRANDS if b in name_u), "")
    if not brand:
        return False
    # Require full brand phrase in sibling descriptions (e.g. "GOLI SODA")
    pattern = re.escape(brand)
    sib = products[products["description"].astype(str).str.upper().str.contains(pattern, na=False)]
    if sib.empty:
        return False
    sold = pd.to_numeric(sib["sold_in_lookback"], errors="coerce").fillna(0)
    return bool((sold > 0).any())

def build_catalog_gap_suggestions(
    catalog: pd.DataFrame,
    products: pd.DataFrame,
    *,
    vendor_name: str,
    vendor_key: str = "",
    force_names: tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """
    Build order rows for catalog products with no POS inventory match.

    Suggests **1 case** (units = catalog case qty). Forced list always included;
    other lines included when a sibling brand SKU already has sales (trend).
    """
    if catalog is None or catalog.empty:
        return pd.DataFrame()

    force = force_names
    if force is None and str(vendor_key).upper() == "ANNAPURNA":
        force = FORCE_ANNAPURNA_CATALOG_SUGGESTS
    force = force or ()
    force_keys = {_norm_key(n) for n in force}

    # Sibling-trend brands (Goli soda) only apply to Annapurna order form.
    sibling_brands = SIBLING_TREND_BRANDS if str(vendor_key).upper() == "ANNAPURNA" else ()

    # Only forced names / sibling-trend brands can become suggestions.
    # Skip full-catalog coverage scans (Premier.xlsx is 2000+ rows → multi-minute).
    if not force_keys and not sibling_brands:
        return pd.DataFrame()

    name_series = catalog["product_name"].astype(str)
    sibling_mask = pd.Series(False, index=catalog.index)
    for brand in sibling_brands:
        sibling_mask = sibling_mask | name_series.str.upper().str.contains(
            re.escape(brand), na=False, regex=True
        )
    if not force_keys and not bool(sibling_mask.any()):
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for _, cat in catalog.iterrows():
        name = str(cat.get("product_name", "")).strip()
        if not name or len(name) < 4:
            continue
        key = _norm_key(name)
        if key in seen:
            continue

        forced = key in force_keys or any(fk in key or key in fk for fk in force_keys)
        maybe_sibling = any(b in name.upper() for b in sibling_brands)
        if not forced and not maybe_sibling:
            continue
        if _already_covered(name, products):
            continue

        trending = _sibling_selling(name, products) if maybe_sibling else False
        if not forced and not trending:
            continue

        units = _case_units(name, cat.get("catalog_pack"))
        upc = _catalog_upc(name)
        note = (
            "Catalog item not in POS yet — back team confirmed available. "
            f"Suggest **1 case** ({units} units)."
        )
        if trending and not forced:
            note = f"Catalog-only; sibling brand is selling — suggest 1 case ({units} units)."

        rows.append(
            {
                "upc": upc,
                "description": name,
                "vendor_name": vendor_name,
                "vendor_key": vendor_key or "",
                "department": "CATALOG SUGGEST",
                "pack_size": units,
                "current_stock": 0.0,
                "stock_for_reorder": 0.0,
                "negative_sold_units": 0,
                "ai_min": units,
                "safety_stock": 0,
                "demand_std": 0.0,
                "lead_time_demand": units,
                "ads": 0.0,
                "lookback_days": 0,
                "sold_in_lookback": 0,
                "total_sold_30d": 0,
                "lead_time_days": 0,
                "planning_cover_days": 0,
                "schedule_known": True,
                "order_cutoff": "",
                "delivery_days": "",
                "delivery_day_labels": "",
                "order_frequency": "",
                "min_days_cover": "",
                "formula_breakdown": f"catalog suggest = 1 case ({units} units)",
                "units_needed": units,
                "formula_raw_need": units,
                "ml_forecast_demand": 0.0,
                "ml_need": 0,
                "horizon_uplift": 1.0,
                "uplift_note": "catalog suggest (no POS history)",
                "order_math_note": note,
                "units_after_uplift": units,
                "uplift_extra_units": 0,
                "ai_need_units": units,
                "need_calc": f"{units}={units}",
                "invoice_cap_note": "",
                "invoice_max_cases": 0.0,
                "invoice_max_units": 0.0,
                "invoice_median_units": 0.0,
                "invoice_order_count": 0,
                "order_qty": units,
                "cases_to_order": pack_units_ordered(units, units) if units > 0 else 1.0,
                "pack_rounding_note": f"1 case of {units}",
                "reorder_needed": True,
                "reorder_reason": "catalog_suggest",
                "catalog_matched": True,
                "catalog_match_score": 100.0,
                "catalog_product_name": name,
                "case_cost": None,
                "est_order_cost": None,
                "similar_alternatives": "",
                "other_brands_stock": "",
                "similar_group_size": 0,
                "pos_vendor_name": "CATALOG-ONLY",
            }
        )
        seen.add(key)

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    # cases_to_order should be 1
    out["cases_to_order"] = 1.0
    return out
