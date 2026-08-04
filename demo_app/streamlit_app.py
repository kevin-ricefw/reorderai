"""
Reorder AI — Streamlit demo (calls the FastAPI endpoints).

  streamlit run demo_app/streamlit_app.py
"""

from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd
import requests
import streamlit as st

DEFAULT_API = "http://74.249.36.238:8000"
# (connect timeout, read timeout) — detect-order can be slow on cold start
TIMEOUT = (10, 300)

DEFAULT_TOOLS = [
    "get_order_run",
    "list_order_lines",
    "why_item",
    "get_item_details",
    "list_expiry_capped",
    "compare_items",
]

# Table columns: API field → clear display name.
ORDER_TABLE_COLS: list[tuple[str, str]] = [
    ("line_action", "Action"),
    ("urgency", "Urgency"),
    ("description", "Product"),
    ("upc", "UPC"),
    ("available_stock", "On Hand"),
    ("days_of_supply", "Days of Supply"),
    ("ads", "ADS / day"),
    ("ads_times_x", "ADS × X"),
    ("selling_days", "Selling Days"),
    ("zero_sales_days", "Zero-Sale Days"),
    ("reorder_point", "Reorder Point (ROP)"),
    ("below_reorder_point", "Below ROP"),
    ("desired_stock", "Desired Stock"),
    ("projected_stock_at_arrival", "Stock at Arrival"),
    ("qty_to_order", "Qty to Order"),
    ("cases_to_order", "Cases to Order"),
    ("cover_demand_ads", "Cover Demand (X−L)"),
    ("lead_demand_ads", "Lead Demand (L)"),
    ("safety_stock", "SS(L)"),
    ("safety_stock_cover", "SS(X−L)"),
    ("ads_cover_qty", "ADS Cover (X−L)+SS"),
    ("ai_target_qty", "AI Target (cover+SS+uplift)"),
    ("uplift_multiplier", "Uplift ×"),
    ("upcoming_festivals", "Festivals in X"),
    ("last_pallet_qty", "Last Invoice Qty"),
    ("demand_class", "Demand Class"),
    ("justification", "Justification"),
]


def order_items_dataframe(items: list[dict[str, Any]], *, x_days: int | None = None) -> pd.DataFrame:
    """Build display table with clean column names."""
    df = pd.DataFrame(items)
    if df.empty:
        return df
    keep = [src for src, _ in ORDER_TABLE_COLS if src in df.columns]
    out = df[keep].rename(columns={src: label for src, label in ORDER_TABLE_COLS if src in keep})
    if x_days is not None and x_days > 0:
        x_i = int(x_days)
        x_label = f"for {x_i} days"
        out = out.rename(
            columns={
                "ADS × X": f"ADS × {x_i}d",
                "ADS Cover Qty (for X days)": f"ADS Cover Qty ({x_label})",
                "P50 Demand (for X days)": f"P50 Demand ({x_label})",
                "P90 Demand (for X days)": f"P90 Demand ({x_label})",
                "AI Target Qty (for X days)": f"AI Target Qty ({x_label})",
            }
        )
    return out


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def item_math_explain_table(
    it: dict[str, Any],
    *,
    lead: int,
    cover: int,
    x_days: int,
) -> pd.DataFrame:
    """One row per order-table field: value + plain-English meaning + formula."""
    on_hand = _num(it.get("available_stock"))
    ads = _num(it.get("ads"))
    lead_ads = _num(it.get("lead_demand_ads"))
    cover_ads = _num(it.get("cover_demand_ads"))
    ss_l = _num(it.get("safety_stock"))
    ads_cover = _num(it.get("ads_cover_qty"))
    ss_c = _num(it.get("safety_stock_cover"))
    if ss_c <= 0:
        # older API without safety_stock_cover: back out from ADS cover
        ss_c = max(0.0, round(ads_cover - cover_ads, 4))
    rop = _num(it.get("reorder_point"))
    below = bool(it.get("below_reorder_point"))
    min_oh = _num(it.get("min_on_hand"))
    min_src = str(it.get("min_on_hand_source") or "none")
    max_oh = _num(it.get("wecomm_max_on_hand"))
    below_min = bool(it.get("below_min_on_hand"))
    desired = _num(it.get("desired_stock"))
    if desired <= 0:
        desired = max(_num(it.get("ai_target_qty")), min_oh)
    uplift = _num(it.get("uplift_multiplier"), 1.0)
    uplift_rule = it.get("uplift_rule") or "none"
    p50 = _num(it.get("p50_demand"))
    p90 = _num(it.get("p90_demand"))
    target = _num(it.get("ai_target_qty"))
    at_arrival = _num(it.get("projected_stock_at_arrival"))
    raw = _num(it.get("raw_qty_to_order"))
    qty = _num(it.get("qty_to_order"))
    cases = _num(it.get("cases_to_order"))
    pack = max(int(_num(it.get("box_qty"), 1)), 1)
    dclass = it.get("demand_class") or "—"
    last_inv = it.get("last_pallet_qty")
    upc = it.get("upc") or "—"
    name = str(it.get("description") or it.get("item_id") or "Item")
    model_note = {
        "smooth": "LightGBM (steady daily sales)",
        "intermittent": "Croston-SBA (sparse / stop-start sales)",
        "lumpy": "TSB (uneven spikes)",
        "erratic": "TSB (high variance)",
        "single_demand_day": "simple rule (almost no history)",
    }.get(str(dclass).lower(), "nightly forecast model for this demand class")
    uplifted_cover = round(cover_ads * uplift, 4)

    rows = [
        {
            "Column": "Product",
            "Value": name,
            "What it means": "Item name from the vendor catalog.",
            "How / where it comes from": "Wecomm products table (linked via product_vendor).",
        },
        {
            "Column": "UPC",
            "Value": upc,
            "What it means": "Barcode used to match sales, stock, and forecasts.",
            "How / where it comes from": "product_barcodes.",
        },
        {
            "Column": "On Hand",
            "Value": f"{on_hand:g}",
            "What it means": (
                "Units in stock right now (can be negative = oversold). "
                "Negatives stay visible; |OH| is counted as sold for ADS. "
                "Order math still floors stock at 0 (deficit not added to PO)."
            ),
            "How / where it comes from": "product_locations.quantity.",
        },
        {
            "Column": "ADS / day",
            "Value": f"{ads:g}",
            "What it means": "Average daily sales speed over the last 90 days.",
            "How / where it comes from": "Sum of units sold in last 90 days ÷ 90 (POS / ai_pos_daily_sales).",
        },
        {
            "Column": f"ADS × {x_days}d",
            "Value": f"{_num(it.get('ads_times_x'), ads * x_days):g}",
            "What it means": (
                "Sanity baseline — plain ADS×X before safety stock, festival uplift, "
                "or ML polish. If final qty is wildly different from this, dig in."
            ),
            "How / where it comes from": f"ADS × X = {ads:g} × {x_days}.",
        },
        {
            "Column": "Selling / zero-sale days",
            "Value": (
                f"{int(_num(it.get('selling_days')))} selling / "
                f"{int(_num(it.get('zero_sales_days')))} zero "
                f"(lookback {int(_num(it.get('sales_lookback_days'), 90))}d)"
            ),
            "What it means": (
                "How often the SKU actually sold. Example: 5 selling days and 85 zero days "
                "means lumpy demand — most days had no sale."
            ),
            "How / where it comes from": "Count of POS days with quantity > 0 vs days with no sale in lookback.",
        },
        {
            "Column": "Festivals in X",
            "Value": str(it.get("upcoming_festivals") or "none / weekends only"),
            "What it means": (
                "Named festivals + weekends inside the next X days. "
                "Raises cover qty only if this SKU has a learned uplift for those tags."
            ),
            "How / where it comes from": (
                f"festival_calendar scan of each day in X={x_days}; "
                f"SKU uplift ×{uplift:g} (rule: {uplift_rule})."
            ),
        },
        {
            "Column": "Lead Demand ADS (L days)",
            "Value": f"{lead_ads:g}",
            "What it means": (
                "Expected sell-through while waiting for the truck. "
                "Burns from on-hand; NOT added to the PO when OH is already 0."
            ),
            "How / where it comes from": f"ADS × L = {ads:g} × {lead}.",
        },
        {
            "Column": "Cover Demand ADS (C days)",
            "Value": f"{cover_ads:g}",
            "What it means": "Expected sales after arrival — this is what the order is sized for.",
            "How / where it comes from": (
                f"ceil(ADS × C) = ceil({ads:g} × {cover}) → {cover_ads:g} whole units."
            ),
        },
        {
            "Column": "Safety Stock SS(L) — ROP only",
            "Value": f"{ss_l:g}",
            "What it means": (
                "Lead-time buffer only. Used in ROP. "
                "Do NOT add this into AI target / order qty."
            ),
            "How / where it comes from": (
                f"SS(L) = Z × σ × √L = 1.65 × {_num(it.get('demand_std')):g} × √{lead} → {ss_l:g}."
            ),
        },
        {
            "Column": "Safety Stock SS(C) — in order",
            "Value": f"{ss_c:g}",
            "What it means": (
                "Cover-period buffer. This IS inside ADS Cover and AI Target. "
                "Larger than SS(L) because √C > √L."
            ),
            "How / where it comes from": (
                f"SS(C) = Z × σ × √C = 1.65 × {_num(it.get('demand_std')):g} × √{cover} → {ss_c:g}."
            ),
        },
        {
            "Column": "Reorder Point (ROP)",
            "Value": f"{rop:g}",
            "What it means": "Lead-time ‘low stock’ warning line — urgency flag, not the order qty.",
            "How / where it comes from": f"ROP = ADS×L + SS(L) = {ads:g}×{lead} + {ss_l:g} → {rop:g}.",
        },
        {
            "Column": "Below Reorder Point",
            "Value": str(below).upper(),
            "What it means": "TRUE = may stock out during lead. Does not change the cover-C order formula.",
            "How / where it comes from": f"On hand < ROP → {on_hand:g} < {rop:g}.",
        },
        {
            "Column": f"ADS Cover Qty (for C={cover} days)",
            "Value": f"{ads_cover:g}",
            "What it means": "Cover need with buffer, before festival uplift (whole units).",
            "How / where it comes from": (
                f"ceil(ADS×C + SS(C)) = ceil({ads:g}×{cover} + {ss_c:g}) → {ads_cover:g}."
            ),
        },
        {
            "Column": "Uplift ×",
            "Value": f"{uplift:g}",
            "What it means": "Weekend / festival lift on expected cover sales only (not on SS).",
            "How / where it comes from": f"Learned SKU uplift (rule: {uplift_rule}). 1.0 = no lift.",
        },
        {
            "Column": f"P50 Demand (for {x_days} days)",
            "Value": f"{p50:g}",
            "What it means": "ML median for X days — reference only; does not set qty.",
            "How / where it comes from": (
                f"Nightly forecast_store (class={dclass} → {model_note}), scaled to X={x_days}."
            ),
        },
        {
            "Column": f"P90 Demand (for {x_days} days)",
            "Value": f"{p90:g}",
            "What it means": "ML high-side for X days — reference only; does not set qty.",
            "How / where it comes from": (
                f"Same nightly file as P50 (class={dclass} → {model_note}), scaled to X={x_days}."
            ),
        },
        {
            "Column": f"AI Target Qty (cover C={cover})",
            "Value": f"{target:g}",
            "What it means": "Cover need after arrival (whole units).",
            "How / where it comes from": (
                f"ceil(ADS×C×uplift + SS(C)) = ceil({ads:g}×{cover}×{uplift:g} + {ss_c:g}) → {target:g}."
            ),
        },
        {
            "Column": "Desired Stock",
            "Value": f"{desired:g}",
            "What it means": "Order-up-to level after arrival (= AI cover target).",
            "How / where it comes from": f"Desired = AI target = {desired:g}.",
        },
        {
            "Column": "Stock at arrival",
            "Value": f"{at_arrival:g}",
            "What it means": "On-hand left when PO arrives after burning lead demand.",
            "How / where it comes from": (
                f"max(0, OH − ADS×L) = max(0, {on_hand:g} − {lead_ads:g}) = {at_arrival:g}."
            ),
        },
        {
            "Column": "Qty to Order",
            "Value": f"{qty:g}",
            "What it means": "Final units to buy after pack/case rounding.",
            "How / where it comes from": (
                f"raw need = max(0, {desired:g} − {at_arrival:g}) = {raw:g}; "
                f"round UP to full cases (pack {pack}) → {qty:g} units."
            ),
        },
        {
            "Column": "Cases to Order",
            "Value": f"{cases:g}",
            "What it means": "Whole cases to buy (never fractional like 0.6).",
            "How / where it comes from": f"ceil(raw need ÷ pack {pack}) = {cases:g}.",
        },
        {
            "Column": "Last Invoice Qty",
            "Value": f"{last_inv:g}" if last_inv is not None else "—",
            "What it means": "Reference only — last purchased qty. Does not set the order.",
            "How / where it comes from": "Vendor PO history or Past Invoices fallback.",
        },
        {
            "Column": "Demand Class",
            "Value": str(dclass),
            "What it means": "How sales behave — picks which ML model wrote P50/P90.",
            "How / where it comes from": f"Syntetos–Boylan class from sales history → {model_note}.",
        },
        {
            "Column": "Window X",
            "Value": f"{x_days} days (L={lead} + C={cover})",
            "What it means": (
                "L = wait/burn; C = order cover. "
                "Order qty uses C (+ SS(C)); L is for burn + ROP only."
            ),
            "How / where it comes from": "Your detect-order inputs: lead_time_days + time_to_cover_days.",
        },
    ]
    return pd.DataFrame(rows)


def format_item_math(
    it: dict[str, Any],
    *,
    lead: int,
    cover: int,
    x_days: int,
) -> str:
    """Full step-by-step math with every formula and plugged-in numbers."""
    name = str(it.get("description") or it.get("item_id") or "Item")
    upc = it.get("upc") or "—"
    on_hand = _num(it.get("available_stock"))
    ads = _num(it.get("ads"))
    std = _num(it.get("demand_std"))
    lead_ads = _num(it.get("lead_demand_ads"))
    cover_ads = _num(it.get("cover_demand_ads"))
    ss_l = _num(it.get("safety_stock"))
    ss_c = _num(it.get("safety_stock_cover"))
    ads_cover = _num(it.get("ads_cover_qty"))
    if ss_c <= 0 and ads_cover > 0:
        ss_c = max(0.0, round(ads_cover - cover_ads, 4))
    rop = _num(it.get("reorder_point"))
    below = bool(it.get("below_reorder_point"))
    desired = _num(it.get("desired_stock"))
    if desired <= 0:
        desired = _num(it.get("ai_target_qty"))
    uplift = _num(it.get("uplift_multiplier"), 1.0)
    uplift_rule = it.get("uplift_rule") or "none"
    p50 = _num(it.get("p50_demand"))
    p90 = _num(it.get("p90_demand"))
    target = _num(it.get("ai_target_qty"))
    at_arrival = _num(it.get("projected_stock_at_arrival"))
    raw = _num(it.get("raw_qty_to_order"))
    qty = _num(it.get("qty_to_order"))
    cases = _num(it.get("cases_to_order"))
    pack = max(int(_num(it.get("box_qty"), 1)), 1)
    dclass = it.get("demand_class") or "—"
    action = it.get("line_action") or "—"
    urgency = it.get("urgency") or "—"
    dos = it.get("days_of_supply")
    dos_after = it.get("days_of_supply_after_order")
    dos_s = f"{dos:g}" if dos is not None else "n/a (ADS≈0)"
    dos_after_s = f"{dos_after:g}" if dos_after is not None else "n/a"
    notes = it.get("validation_notes") or []
    notes_s = "; ".join(str(n) for n in notes) if notes else "none"
    last_inv = it.get("last_pallet_qty")
    last_inv_s = f"{_num(last_inv):g}" if last_inv is not None else "—"
    lookback = int(_num(it.get("sales_lookback_days"), 90))
    selling_days = int(_num(it.get("selling_days")))
    zero_days = int(_num(it.get("zero_sales_days")))
    if zero_days <= 0 and lookback > 0:
        zero_days = max(lookback - selling_days, 0)
    total_sold = _num(it.get("total_units_sold"))
    avg_sell_day = _num(it.get("avg_units_on_selling_day"))
    if avg_sell_day <= 0 and selling_days > 0 and total_sold > 0:
        avg_sell_day = total_sold / selling_days
    fests = str(it.get("upcoming_festivals") or "").strip() or "none named (weekends still scanned)"
    fest_applied = bool(it.get("festival_uplift_applied"))
    just = str(it.get("justification") or "").strip()
    z = 1.65
    # Reconstruct intermediate floats for display (API stores ceil'd cover)
    lead_ads_raw = ads * lead
    cover_ads_raw = ads * cover
    uplifted_sales_raw = cover_ads_raw * uplift
    ads_cover_raw = cover_ads_raw + ss_c
    ai_raw = uplifted_sales_raw + ss_c
    at_arrival_raw = max(0.0, on_hand - ads * lead)
    raw_need_raw = max(0.0, desired - at_arrival)
    cases_calc = int(math.ceil(raw / pack - 1e-9)) if raw > 1e-9 else 0
    sigma_note = f"{std:g}" if std > 0 else f"≈0.3×ADS={0.3 * ads:g}"
    pattern_bar = (
        f"sold on **{selling_days}** of **{lookback}** days "
        f"(no sale on **{zero_days}** days)"
    )
    if selling_days > 0 and lookback > 0 and selling_days / lookback < 0.25:
        pattern_bar += " — **intermittent / lumpy** (most days had zero sales)"
    fest_effect = (
        f"SKU uplift **applied** ×{uplift:g} (rule: `{uplift_rule}`) — "
        "this item historically spikes on those tags"
        if fest_applied and uplift > 1.0
        else (
            f"calendar checked; **no SKU uplift** for this item (×{uplift:g}) — "
            "festival alone does not raise qty unless this SKU learned a lift"
        )
    )

    return f"""### {name}
| | |
|:--|:--|
| **UPC** | `{upc}` |
| **Demand class** | `{dclass}` |
| **Action / Urgency** | `{action}` / `{urgency}` |
| **Inputs** | L = **{lead}** d · C = **{cover}** d · X = L+C = **{x_days}** d · pack = **{pack}** |
| **Days of supply now** | `{dos_s}` = OH ÷ ADS = {on_hand:g} ÷ {ads:g} |
| **Days of supply after order** | `{dos_after_s}` ≈ (at arrival + qty) ÷ ADS |
| **Sales pattern** | {pattern_bar} |

---

### A) Demand speed + sales pattern
**1. ADS (avg daily sales, last {lookback}d)**  
`ADS = (units sold in lookback) ÷ lookback days`  
→ total sold **`{total_sold:g}`** ÷ **{lookback}** → **ADS = `{ads:g}` units/day**  
Daily σ used in safety stock → **σ = `{sigma_note}`**

**1a. Sanity baseline — ADS × X (before SS / uplift / ML)**  
`ADS × X = {ads:g} × {x_days} = {_num(it.get('ads_times_x'), ads * x_days):g}`  
→ Use this as a gut-check: final qty should be in the same ballpark unless SS/uplift/pack round moves it.

**1b. How often it actually sold (so you can see the pattern)**  
In the last **{lookback}** days this SKU sold on **`{selling_days}`** day(s);  
**`{zero_days}`** day(s) had **no sale**.  
On a selling day, avg ≈ **`{avg_sell_day:g}`** units.  
*(Example reading: “sold on 5 days, rest of the 90 had zero” = lumpy demand — ADS is low because zeros pull the average down.)*

---

### B) Lead window L — burn + ROP (trigger only, not order qty)
**2. Lead demand (expected sell while waiting)**  
`Lead demand = ADS × L = {ads:g} × {lead} = {lead_ads_raw:g}`  
→ stored **`{lead_ads:g}`**

**3. Safety stock for lead SS(L)**  
`SS(L) = Z × σ × √L` with Z≈{z:g} (≈95% service)  
`SS(L) = {z:g} × {std:g} × √{lead}` → **`{ss_l:g}`**  
*(ROP buffer only — do **not** add this into the PO by itself)*

**4. Reorder point (when to care)**  
`ROP = ADS×L + SS(L) = {lead_ads_raw:g} + {ss_l:g} = {lead_ads_raw + ss_l:g}`  
→ **ROP = `{rop:g}`**  
Below ROP? `OH < ROP` → `{on_hand:g} < {rop:g}` → **`{str(below).upper()}`**

**5. Stock left when truck arrives**  
`stock_at_arrival = max(0, OH − ADS×L) = max(0, {on_hand:g} − {lead_ads_raw:g})`  
→ **`{at_arrival:g}`** (API: `{at_arrival:g}`; raw calc `{at_arrival_raw:g}`)  
If OH was already 0, arrival stock stays 0 — we do **not** add lead demand onto the PO.

---

### C) Cover window C — what we order for
**6. Cover demand (sales after arrival)**  
`Cover demand (raw) = ADS × C = {ads:g} × {cover} = {cover_ads_raw:g}`  
`Cover demand (stored) = ceil(ADS×C)` → **`{cover_ads:g}`** whole units

**7. Safety stock for cover SS(C)**  
`SS(C) = Z × σ × √C = {z:g} × {std:g} × √{cover}` → **`{ss_c:g}`**  
*(this buffer **is** inside the order target)*

**8. Festival / weekend calendar in the next X = {x_days} days**  
Upcoming: **{fests}**  
Effect on this SKU: {fest_effect}  
*('Today' = API `as_of_date` from the server clock in `REORDER_TZ`, default America/Detroit (Michigan) — not the browser. Engine scans each day in X; only SKUs with a learned lift raise cover sales.)*

**9. Uplift on expected cover sales only**  
`uplift = {uplift:g}` (rule: `{uplift_rule}`) — multiplies **ADS×C**, not SS  
`uplifted cover sales = ADS×C×uplift = {cover_ads_raw:g} × {uplift:g} = {uplifted_sales_raw:g}`

**10. ADS cover qty (no uplift, whole units)**  
`ADS cover = ceil(ADS×C + SS(C)) = ceil({cover_ads_raw:g} + {ss_c:g}) = ceil({ads_cover_raw:g})`  
→ **`{ads_cover:g}`**

**11. AI cover target / Desired stock (whole units)**  
`AI target = ceil(ADS×C×uplift + SS(C)) = ceil({uplifted_sales_raw:g} + {ss_c:g}) = ceil({ai_raw:g})`  
→ **AI target = `{target:g}`**  
→ **Desired = `{desired:g}`** (same as AI target; min-on-hand not used)

---

### D) Order qty — full cases only
**12. Raw need**  
`raw_need = max(0, Desired − stock_at_arrival) = max(0, {desired:g} − {at_arrival:g})`  
→ **`{raw:g}`**

**13. Round UP to full cases**  
`cases = ceil(raw_need ÷ pack) = ceil({raw:g} ÷ {pack})` → **`{cases:g}`** (whole; never 0.6)  
`qty_to_order = cases × pack = {cases:g} × {pack}` → **`{qty:g}` units**  
(check: ceil path → {cases_calc} cases)

**14. Final recommendation**  
**Action `{action}`** · **Urgency `{urgency}`** · buy **`{qty:g}` units** = **`{cases:g}` cases**

---

### E) Justification (why this action)
{just if just else "_No justification text returned._"}

---

### F) Reference only (does **not** set qty)
**15. ML for X = {x_days}d** · class `{dclass}`  
P50 = `{p50:g}` · P90 = `{p90:g}` — shown for comparison only  
**16. Last invoice qty** = `{last_inv_s}` — reference only  

**17. Notes** — {notes_s}

---
**One-line summary:**  
ROP `{rop:g}` says *when* to worry (`below={str(below).upper()}`).  
Desired `{desired:g}` = cover for **C={cover}** days after arrival.  
Order = round up (`{raw:g}` → `{qty:g}` units / `{cases:g}` cases).  
Sales pattern: `{selling_days}` selling / `{zero_days}` zero-sale days in `{lookback}`d.
"""

# ── HTTP helpers ─────────────────────────────────────────────────────────────


def api_base() -> str:
    return st.session_state.get("api_base", DEFAULT_API).rstrip("/")


def _get(path: str, params: dict | None = None) -> dict[str, Any]:
    r = requests.get(f"{api_base()}{path}", params=params or {}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    r = requests.post(f"{api_base()}{path}", json=body, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def safe_call(fn, *args, **kwargs) -> dict[str, Any] | None:
    try:
        return fn(*args, **kwargs)
    except requests.Timeout:
        st.error(
            f"API timed out at {api_base()}. "
            "Retry, or check that the API is running."
        )
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text if exc.response is not None else str(exc)
        st.error(f"API error {exc.response.status_code if exc.response else ''}: {detail}")
    except requests.RequestException as exc:
        st.error(f"Cannot reach API at {api_base()} — is uvicorn running? ({exc})")
    return None


def cached_tool_names() -> list[str]:
    if "tool_names" in st.session_state:
        return st.session_state.tool_names
    data = safe_call(_get, "/api/chatbot/tools")
    names = [t["name"] for t in (data or {}).get("tools", [])] or list(DEFAULT_TOOLS)
    st.session_state.tool_names = names
    return names


# ── Page setup ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Reorder AI",
    page_icon="🔷",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Blue visual system (overrides Streamlit default red/pink primary)
st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,600;700&display=swap');

html, body, [class*="css"] {
  font-family: "DM Sans", "Segoe UI", sans-serif;
}

.stApp {
  background:
    radial-gradient(1200px 500px at 10% -10%, #cfe4f7 0%, transparent 55%),
    radial-gradient(900px 420px at 100% 0%, #d9ebf8 0%, transparent 50%),
    linear-gradient(180deg, #eef4fa 0%, #f7fafc 40%, #f4f7fb 100%);
}

/* Primary buttons → blue */
.stButton > button[kind="primary"],
.stButton > button[data-testid="baseButton-primary"] {
  background: linear-gradient(135deg, #1B6CA8 0%, #0E4D7A 100%) !important;
  border: none !important;
  color: #fff !important;
  font-weight: 600 !important;
  box-shadow: 0 6px 18px rgba(14, 77, 122, 0.28);
}
.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="baseButton-primary"]:hover {
  background: linear-gradient(135deg, #2280c4 0%, #155a8a 100%) !important;
}

/* Secondary / default buttons */
.stButton > button {
  border-radius: 10px !important;
  border-color: #9ec0db !important;
  color: #0F2744 !important;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
  gap: 8px;
  background: transparent;
}
.stTabs [data-baseweb="tab"] {
  background: rgba(255,255,255,0.55);
  border-radius: 10px 10px 0 0;
  padding: 10px 18px;
  color: #3a5a78;
  font-weight: 600;
}
.stTabs [aria-selected="true"] {
  background: #fff !important;
  color: #1B6CA8 !important;
  box-shadow: 0 -2px 0 #1B6CA8 inset;
}

/* Sidebar */
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0E4D7A 0%, #143d63 45%, #0f2f4d 100%);
}
section[data-testid="stSidebar"] * {
  color: #eaf3fb !important;
}
section[data-testid="stSidebar"] .stTextInput input {
  background: rgba(255,255,255,0.12) !important;
  color: #fff !important;
  border: 1px solid rgba(255,255,255,0.25) !important;
}
section[data-testid="stSidebar"] .stButton > button {
  background: rgba(255,255,255,0.12) !important;
  color: #fff !important;
  border: 1px solid rgba(255,255,255,0.28) !important;
}
section[data-testid="stSidebar"] pre,
section[data-testid="stSidebar"] code {
  background: rgba(0,0,0,0.25) !important;
  color: #d6ebfa !important;
}

/* Metrics */
div[data-testid="stMetric"] {
  background: #fff;
  border: 1px solid #d5e4f2;
  border-radius: 14px;
  padding: 12px 14px;
  box-shadow: 0 8px 24px rgba(15, 39, 68, 0.06);
}
div[data-testid="stMetric"] label { color: #5a7a96 !important; }
div[data-testid="stMetric"] [data-testid="stMetricValue"] {
  color: #0E4D7A !important;
  font-weight: 700;
}

/* Dataframes / inputs */
div[data-testid="stDataFrame"] {
  border: 1px solid #d5e4f2;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(15, 39, 68, 0.05);
}

/* Hero */
.rai-hero {
  background: linear-gradient(120deg, #0E4D7A 0%, #1B6CA8 55%, #3a8fd1 100%);
  color: #fff;
  border-radius: 18px;
  padding: 1.6rem 1.8rem 1.4rem;
  margin-bottom: 1.1rem;
  box-shadow: 0 16px 40px rgba(14, 77, 122, 0.28);
  position: relative;
  overflow: hidden;
}
.rai-hero::after {
  content: "";
  position: absolute;
  right: -40px; top: -40px;
  width: 180px; height: 180px;
  border-radius: 50%;
  background: rgba(255,255,255,0.12);
}
.rai-hero h1 {
  font-family: "Fraunces", Georgia, serif;
  font-size: 2rem;
  margin: 0 0 0.35rem 0;
  letter-spacing: -0.02em;
}
.rai-hero p {
  margin: 0;
  opacity: 0.92;
  max-width: 46rem;
  font-size: 1.02rem;
}
.rai-chip {
  display: inline-block;
  margin-top: 0.85rem;
  background: rgba(255,255,255,0.16);
  border: 1px solid rgba(255,255,255,0.28);
  border-radius: 999px;
  padding: 0.25rem 0.75rem;
  font-size: 0.85rem;
  font-weight: 600;
}
.rai-panel {
  background: rgba(255,255,255,0.72);
  border: 1px solid #d5e4f2;
  border-radius: 14px;
  padding: 0.85rem 1rem;
  margin: 0.6rem 0 1rem 0;
}
</style>
""",
    unsafe_allow_html=True,
)

if "api_base" not in st.session_state:
    st.session_state.api_base = DEFAULT_API
if "order_result" not in st.session_state:
    st.session_state.order_result = None
if "chat_log" not in st.session_state:
    st.session_state.chat_log = []
if "vendors" not in st.session_state:
    st.session_state.vendors = []


# ── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Reorder AI")
    st.caption("Blue ops console · FastAPI demo")

    st.session_state.api_base = st.text_input(
        "API base URL",
        value=st.session_state.api_base,
        help="Live VM default: http://74.249.36.238:8000",
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Health", use_container_width=True):
            data = safe_call(_get, "/api/health")
            if data:
                st.success(data)
    with c2:
        if st.button("DB", use_container_width=True):
            data = safe_call(_get, "/api/db-health")
            if data:
                if data.get("ok"):
                    st.success("DB connected")
                else:
                    st.warning(data)

    st.divider()
    st.markdown("**Endpoints**")
    st.code(
        "GET  /api/health\n"
        "GET  /api/db-health\n"
        "GET  /api/detect-order\n"
        "POST /api/detect-order\n"
        "GET  /api/detect-order/runs/{id}\n"
        "GET  /api/chatbot/tools\n"
        "POST /api/chatbot/ask\n"
        "POST /api/chatbot/tool",
        language="text",
    )

    if st.button("Open API docs", use_container_width=True):
        st.markdown(f"[Swagger →]({api_base()}/docs)")


# ── Hero ─────────────────────────────────────────────────────────────────────

st.markdown(
    """
<div class="rai-hero">
  <h1>Reorder AI</h1>
  <p>Vendor reorder recommendations from live stock, ADS, and nightly forecasts —
  sized for lead time + cover, rounded to full cases.</p>
  <span class="rai-chip">Detect order · Festival calendar · Nightly ML</span>
</div>
""",
    unsafe_allow_html=True,
)


# ── Tabs ─────────────────────────────────────────────────────────────────────

tab_order, tab_chat, tab_run, tab_tools = st.tabs(
    ["1 · Detect order", "2 · Chatbot", "3 · Saved run", "4 · Tools list"]
)


# ── Tab 1: Detect order ──────────────────────────────────────────────────────

with tab_order:
    st.subheader("Detect order")
    st.markdown(
        '<div class="rai-panel">'
        "Pick a vendor, set <b>lead time (L)</b> and <b>days to cover (C)</b>. "
        "Order window is <b>X = L + C</b>. Qty rounds <b>up to full cases</b>."
        "</div>",
        unsafe_allow_html=True,
    )

    top = st.columns([1, 1, 2])
    with top[0]:
        if st.button("Load vendors", type="secondary"):
            data = safe_call(_get, "/api/detect-order")
            if data is not None:
                vendors = data.get("vendors") or []
                st.session_state.vendors = vendors
                st.success(f"Loaded {len(vendors)} vendors")
    with top[1]:
        st.caption(f"{len(st.session_state.vendors)} vendors in session")

    vendors = st.session_state.vendors
    vendor_labels = [
        f"{v.get('vendor_name')}  (id={v.get('vendor_id')})" for v in vendors
    ]
    selected_idx = 0
    if vendor_labels:
        # Prefer HOS if present
        for i, v in enumerate(vendors):
            if "HOS" in str(v.get("vendor_name", "")).upper():
                selected_idx = i
                break
        choice = st.selectbox("Vendor", vendor_labels, index=selected_idx)
        vendor = vendors[vendor_labels.index(choice)]
    else:
        st.info("Click **Load vendors** first (needs live DB + tunnel).")
        vendor = {"vendor_id": "18", "vendor_name": "HOS (LAXMI)"}
        st.text_input("Vendor id (manual)", value=vendor["vendor_id"], key="manual_vid")
        st.text_input("Vendor name (manual)", value=vendor["vendor_name"], key="manual_vname")
        vendor = {
            "vendor_id": st.session_state.get("manual_vid", "18"),
            "vendor_name": st.session_state.get("manual_vname", "HOS (LAXMI)"),
        }

    c_l, c_c, c_x = st.columns(3)
    with c_l:
        lead = st.number_input("Lead time L (days)", min_value=0, max_value=60, value=4, step=1)
    with c_c:
        cover = st.number_input("Days to cover C", min_value=0, max_value=60, value=3, step=1)
    with c_x:
        st.metric("Order window X = L + C", f"{int(lead) + int(cover)} days")

    include_zero = st.checkbox(
        "Include full catalog (SKIP / already covered)",
        value=False,
        help="Default shows ORDER + WATCH only. Turn on to audit every SKU.",
    )

    if st.button("Run detect-order", type="primary", use_container_width=True):
        with st.spinner("Calling POST /api/detect-order …"):
            body = {
                "vendor_id": str(vendor.get("vendor_id")),
                "vendor_name": str(vendor.get("vendor_name")),
                "lead_time_days": int(lead),
                "time_to_cover_days": int(cover),
                "include_zero_orders": bool(include_zero),
            }
            data = safe_call(_post, "/api/detect-order", body)
            if data is not None:
                st.session_state.order_result = data
                st.session_state.chat_log = []
                st.session_state.pop("math_selected_labels", None)
                st.success(
                    f"OK — {data.get('order_line_count')} / "
                    f"{data.get('catalog_item_count') or data.get('item_count')} SKUs · "
                    f"{data.get('total_units_to_order')} units · "
                    f"{data.get('total_cases_to_order')} cases"
                )

    result = st.session_state.order_result
    if result:
        st.warning(
            "Showing the **last detect-order run** stored in this browser session. "
            "After code/math fixes, click **Run detect-order** again — old rows "
            "(e.g. DryApricots qty 100) will not update by themselves."
        )
        st.divider()
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Catalog", result.get("catalog_item_count") or result.get("item_count") or 0)
        m2.metric("Order lines", result.get("order_line_count", 0))
        m3.metric("Units", result.get("total_units_to_order", 0))
        m4.metric("Cases", result.get("total_cases_to_order", 0))
        m5.metric("DB", result.get("db_mode", "—"))
        m6.metric("Forecast", result.get("forecast_mode", "—"))
        as_of = result.get("as_of_date") or "—"
        fests = (result.get("upcoming_festivals") or "").strip() or "none named"
        st.info(
            f"**Calendar as of {as_of}** (API server clock, timezone `REORDER_TZ`, "
            f"default America/Detroit — Michigan — not your browser date). "
            f"**Festivals in next X={result.get('x_days') or (int(lead)+int(cover))} days:** {fests}"
        )
        st.caption(result.get("message") or "")
        rid = result.get("run_id")
        if rid:
            st.markdown(
                f"[Download Excel order sheet]({api_base()}/api/detect-order/runs/{rid}/export.xlsx)"
            )

        items = result.get("items") or []
        if items:
            x_days = int(result.get("x_days") or (int(lead) + int(cover)))
            st.caption(
                f"Columns use window **X = {x_days} days** (L={result.get('lead_time_days', lead)} "
                f"+ C={result.get('time_to_cover_days', cover)})."
            )
            st.dataframe(
                order_items_dataframe(items, x_days=x_days),
                use_container_width=True,
                hide_index=True,
                height=480,
            )
            # Local Excel download (same columns)
            try:
                from api.services.order_export import order_run_to_excel_bytes

                xbytes = order_run_to_excel_bytes(result)
                st.download_button(
                    "Download Excel (Order Sheet)",
                    data=xbytes,
                    file_name=f"order_{(result.get('vendor') or {}).get('vendor_name', 'vendor')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            except Exception as exc:
                st.caption(f"Excel download unavailable: {exc}")

            st.divider()
            st.subheader("Item math (step-by-step)")
            st.write(
                "Select one or more products, then click **Show math** to see the full "
                "calculation for each row (ADS → ROP → AI target → qty)."
            )

            # Prefer order lines first in the picker
            ordered = sorted(
                items,
                key=lambda x: (
                    0 if _num(x.get("qty_to_order")) > 0 else 1,
                    str(x.get("description") or ""),
                ),
            )
            label_to_item: dict[str, dict[str, Any]] = {}
            for it in ordered:
                label = (
                    f"{it.get('description')}  |  on-hand={_num(it.get('available_stock')):g}  |  "
                    f"order={_num(it.get('qty_to_order')):g}"
                )
                # uniquify if duplicate names
                base = label
                n = 2
                while label in label_to_item:
                    label = f"{base} ({n})"
                    n += 1
                label_to_item[label] = it

            picked = st.multiselect(
                "Select items",
                options=list(label_to_item.keys()),
                default=[],
                key="math_item_pick",
                help="Pick products from this run to inspect their math.",
            )
            show_math = st.button(
                "Show math for selected items",
                type="primary",
                key="show_item_math",
                use_container_width=True,
            )
            if show_math:
                if not picked:
                    st.warning("Select at least one item first.")
                else:
                    st.session_state["math_selected_labels"] = list(picked)

            # Persist last selection after click
            labels_to_show = st.session_state.get("math_selected_labels") or []
            # Keep in sync if user changes multiselect and clicks again
            if show_math and picked:
                labels_to_show = list(picked)

            L_run = int(result.get("lead_time_days") or lead)
            C_run = int(result.get("time_to_cover_days") or cover)

            if labels_to_show:
                st.caption(
                    f"Showing math for {len(labels_to_show)} item(s) · X = {x_days} days. "
                    "The table below explains **every order-sheet column** (value + meaning + source)."
                )
                for lab in labels_to_show:
                    it = label_to_item.get(lab)
                    if it is None:
                        continue
                    with st.container(border=True):
                        st.markdown(
                            format_item_math(
                                it, lead=L_run, cover=C_run, x_days=x_days
                            )
                        )
                        st.markdown("#### Full column explanation (same fields as the order table)")
                        st.dataframe(
                            item_math_explain_table(
                                it, lead=L_run, cover=C_run, x_days=x_days
                            ),
                            use_container_width=True,
                            hide_index=True,
                            height=560,
                        )
                        if it.get("justification"):
                            with st.expander("Justification text"):
                                st.write(it.get("justification"))
        else:
            st.warning("No order lines in this response.")


# ── Tab 2: Chatbot ───────────────────────────────────────────────────────────

with tab_chat:
    st.subheader("Investigate chatbot (run-scoped tools)")
    run_id = (st.session_state.order_result or {}).get("run_id")
    if not run_id:
        st.info("Run detect-order first so we have a `run_id`.")
    else:
        st.code(run_id, language="text")

        st.markdown("Ask in plain English (routes to fixed tools only):")
        q = st.text_input(
            "Question",
            placeholder="e.g. why item 1470 / show expiry capped / summarize order",
            key="chat_q",
        )
        if st.button("Ask chatbot", type="primary") and q.strip():
            with st.spinner("POST /api/chatbot/ask …"):
                data = safe_call(
                    _post,
                    "/api/chatbot/ask",
                    {"run_id": run_id, "question": q.strip()},
                )
                if data is not None:
                    st.session_state.chat_log.append(
                        {"role": "user", "text": q.strip()}
                    )
                    st.session_state.chat_log.append(
                        {
                            "role": "bot",
                            "tool": data.get("tool"),
                            "result": data.get("result"),
                            "message": data.get("message"),
                        }
                    )

        st.markdown("Or call a tool directly:")
        tool_names = cached_tool_names()
        tc1, tc2, tc3 = st.columns(3)
        with tc1:
            tool = st.selectbox("Tool", tool_names)
        with tc2:
            item_id = st.text_input("item_id (if needed)", value="")
        with tc3:
            item_id_b = st.text_input("item_id_b (compare)", value="")

        if st.button("Call tool"):
            body: dict[str, Any] = {"run_id": run_id, "tool": tool, "only_nonzero": True}
            if item_id.strip():
                body["item_id"] = item_id.strip()
                body["item_id_a"] = item_id.strip()
            if item_id_b.strip():
                body["item_id_b"] = item_id_b.strip()
            with st.spinner("POST /api/chatbot/tool …"):
                data = safe_call(_post, "/api/chatbot/tool", body)
                if data is not None:
                    st.session_state.chat_log.append(
                        {
                            "role": "bot",
                            "tool": data.get("tool"),
                            "result": data.get("result"),
                            "message": data.get("message"),
                        }
                    )

        st.divider()
        for entry in reversed(st.session_state.chat_log[-12:]):
            if entry["role"] == "user":
                st.markdown(f"**You:** {entry['text']}")
            else:
                st.markdown(f"**Bot** · tool=`{entry.get('tool')}`")
                st.json(entry.get("result") or {})


# ── Tab 3: Saved run ─────────────────────────────────────────────────────────

with tab_run:
    st.subheader("Load a saved order run")
    default_rid = (st.session_state.order_result or {}).get("run_id") or ""
    rid = st.text_input("run_id", value=default_rid)
    if st.button("GET /api/detect-order/runs/{run_id}") and rid.strip():
        data = safe_call(_get, f"/api/detect-order/runs/{rid.strip()}")
        if data is not None:
            st.session_state.order_result = data
            st.success(f"Loaded run {rid.strip()}")
            st.json(
                {
                    "run_id": data.get("run_id"),
                    "vendor": data.get("vendor"),
                    "x_days": data.get("x_days"),
                    "order_line_count": data.get("order_line_count"),
                    "total_units_to_order": data.get("total_units_to_order"),
                    "db_mode": data.get("db_mode"),
                    "forecast_mode": data.get("forecast_mode"),
                    "message": data.get("message"),
                }
            )
            items = data.get("items") or []
            if items:
                st.dataframe(
                    order_items_dataframe(items, x_days=int(data.get("x_days") or 0) or None),
                    use_container_width=True,
                    hide_index=True,
                )


# ── Tab 4: Tools ─────────────────────────────────────────────────────────────

with tab_tools:
    st.subheader("Approved chatbot tools")
    if st.button("Refresh tools"):
        st.session_state.pop("tool_names", None)
        st.session_state.pop("tools_payload", None)
    if "tools_payload" not in st.session_state:
        st.session_state.tools_payload = safe_call(_get, "/api/chatbot/tools")
    data = st.session_state.tools_payload
    if data:
        st.dataframe(pd.DataFrame(data.get("tools") or []), use_container_width=True, hide_index=True)
    st.markdown(
        """
**Demo flow**
1. Sidebar → confirm Health + DB  
2. **Detect order** → Load vendors → pick vendor → set L / C → Run  
3. Review order table (ADS, P50/P90 for X, AI target, qty)  
4. **Chatbot** → ask why / expiry / summary on that `run_id`  
5. **Saved run** → reload any past `run_id`
"""
    )
