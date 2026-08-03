"""
Reorder AI — Streamlit demo (calls the FastAPI endpoints).

  streamlit run demo_app/streamlit_app.py
"""

from __future__ import annotations

import json
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

# Table columns: API field → clear display name (no justification column).
ORDER_TABLE_COLS: list[tuple[str, str]] = [
    ("line_action", "Action"),
    ("urgency", "Urgency"),
    ("description", "Product"),
    ("upc", "UPC"),
    ("available_stock", "On Hand"),
    ("days_of_supply", "Days of Supply"),
    ("ads", "ADS (units/day)"),
    ("reorder_point", "Reorder Point (ROP)"),
    ("below_reorder_point", "Below ROP"),
    ("min_on_hand", "Min On Hand"),
    ("wecomm_max_on_hand", "Max On Hand"),
    ("desired_stock", "Desired Stock"),
    ("projected_stock_at_arrival", "Stock at Arrival"),
    ("qty_to_order", "Qty to Order"),
    ("cases_to_order", "Cases to Order"),
    ("cover_demand_ads", "Cover Demand (C)"),
    ("lead_demand_ads", "Lead Demand (L)"),
    ("safety_stock", "SS(L)"),
    ("safety_stock_cover", "SS(C)"),
    ("ads_cover_qty", "ADS Cover Qty"),
    ("ai_target_qty", "AI Cover Target"),
    ("uplift_multiplier", "Uplift ×"),
    ("last_pallet_qty", "Last Invoice Qty"),
    ("demand_class", "Demand Class"),
]


def order_items_dataframe(items: list[dict[str, Any]], *, x_days: int | None = None) -> pd.DataFrame:
    """Build display table with clean column names; omit justification."""
    df = pd.DataFrame(items)
    if df.empty:
        return df
    keep = [src for src, _ in ORDER_TABLE_COLS if src in df.columns]
    out = df[keep].rename(columns={src: label for src, label in ORDER_TABLE_COLS if src in keep})
    if x_days is not None and x_days > 0:
        # Make X concrete in headers, e.g. "for 17 days"
        x_label = f"for {int(x_days)} days"
        out = out.rename(
            columns={
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
            "What it means": "Units in stock right now (negatives treated as 0 for ordering).",
            "How / where it comes from": "product_locations.quantity.",
        },
        {
            "Column": "ADS (units/day)",
            "Value": f"{ads:g}",
            "What it means": "Average daily sales speed over the last 90 days.",
            "How / where it comes from": "Sum of units sold in last 90 days ÷ 90 (POS / ai_pos_daily_sales).",
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
            "How / where it comes from": f"ADS × C = {ads:g} × {cover}.",
        },
        {
            "Column": "Safety Stock SS(L) — ROP only",
            "Value": f"{ss_l:g}",
            "What it means": (
                "Lead-time buffer only. Used in ROP. "
                "Do NOT add this into AI target / order qty."
            ),
            "How / where it comes from": f"SS(L) = Z × σ × √L  (Z≈1.65). Here ≈ {ss_l:g}.",
        },
        {
            "Column": "Safety Stock SS(C) — in order",
            "Value": f"{ss_c:g}",
            "What it means": (
                "Cover-period buffer. This IS inside ADS Cover and AI Target. "
                "Larger than SS(L) because √C > √L."
            ),
            "How / where it comes from": f"SS(C) = Z × σ × √C  (C={cover}). Here ≈ {ss_c:g}.",
        },
        {
            "Column": "Reorder Point (ROP)",
            "Value": f"{rop:g}",
            "What it means": "Lead-time ‘low stock’ warning line — urgency flag, not the order qty.",
            "How / where it comes from": f"ROP = ADS×L + SS(L) = {lead_ads:g} + {ss_l:g}.",
        },
        {
            "Column": "Below Reorder Point",
            "Value": str(below).upper(),
            "What it means": "TRUE = may stock out during lead. Does not change the cover-C order formula.",
            "How / where it comes from": f"On hand < ROP → {on_hand:g} < {rop:g}.",
        },
        {
            "Column": "Min On Hand",
            "Value": f"{min_oh:g}",
            "What it means": (
                "Wecomm floor only (if set). ROP is NOT used as min — that caused overstock."
            ),
            "How / where it comes from": (
                f"Source={min_src}. max(products.min_on_hand, location min_quantity)."
            ),
        },
        {
            "Column": "Max On Hand",
            "Value": f"{max_oh:g}" if max_oh > 0 else "—",
            "What it means": "Wecomm cap — stops ordering into overstock.",
            "How / where it comes from": "product_locations.max_quantity (min positive across locs).",
        },
        {
            "Column": "Below Min On Hand",
            "Value": str(below_min).upper(),
            "What it means": "TRUE = current on-hand is under the Wecomm min floor.",
            "How / where it comes from": f"On hand < Min → {on_hand:g} < {min_oh:g}.",
        },
        {
            "Column": f"ADS Cover Qty (for C={cover} days)",
            "Value": f"{ads_cover:g}",
            "What it means": "Cover need with buffer, before festival uplift.",
            "How / where it comes from": (
                f"ADS×C + SS(C) = {cover_ads:g} + {ss_c:g} = {ads_cover:g}."
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
            "What it means": "Cover need after arrival (before min floor).",
            "How / where it comes from": (
                f"(ADS×C×uplift) + SS(C) = {uplifted_cover:g} + {ss_c:g} = {target:g}."
            ),
        },
        {
            "Column": "Desired Stock (max cover, min)",
            "Value": f"{desired:g}",
            "What it means": "Order-up-to level after arrival = max(AI cover target, Min).",
            "How / where it comes from": (
                f"max({target:g}, {min_oh:g}) = {desired:g}."
            ),
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
                f"raw = max(0, desired − stock at arrival) = "
                f"max(0, {desired:g} − {at_arrival:g}) = {raw:g}; "
                f"then case round (≥80% of pack {pack})."
            ),
        },
        {
            "Column": "Cases to Order",
            "Value": f"{cases:g}",
            "What it means": "How many cases that qty represents.",
            "How / where it comes from": "Qty to order ÷ pack size after case rounding.",
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
    """Short step-by-step summary above the full explanation table."""
    name = str(it.get("description") or it.get("item_id") or "Item")
    upc = it.get("upc") or "—"
    on_hand = _num(it.get("available_stock"))
    ads = _num(it.get("ads"))
    lead_ads = _num(it.get("lead_demand_ads"))
    cover_ads = _num(it.get("cover_demand_ads"))
    ss_l = _num(it.get("safety_stock"))
    ss_c = _num(it.get("safety_stock_cover"))
    if ss_c <= 0:
        ss_c = max(0.0, round(_num(it.get("ads_cover_qty")) - cover_ads, 4))
    rop = _num(it.get("reorder_point"))
    below = bool(it.get("below_reorder_point"))
    min_oh = _num(it.get("min_on_hand"))
    min_src = str(it.get("min_on_hand_source") or "none")
    max_oh = _num(it.get("wecomm_max_on_hand"))
    below_min = bool(it.get("below_min_on_hand"))
    desired = _num(it.get("desired_stock"))
    if desired <= 0:
        desired = max(_num(it.get("ai_target_qty")), min_oh)
    ads_cover = _num(it.get("ads_cover_qty"))
    uplift = _num(it.get("uplift_multiplier"), 1.0)
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
    dos_s = f"{dos:g}" if dos is not None else "—"
    uplifted_cover = round(cover_ads * uplift, 4)

    return f"""### {name}
**UPC:** `{upc}` · **Demand class:** `{dclass}` · **Action:** `{action}` · **Urgency:** `{urgency}`  
**X = L({lead})+C({cover}) = {x_days} days** · **Days of supply now:** `{dos_s}`

**ROP = trigger only** (when to care). **Qty = cover after arrival** (what to buy).

**1) ADS** → `{ads:g}` / day  
**2) ROP (trigger)** → `ADS×L + SS(L)` = `{rop:g}`, below=`{str(below).upper()}`  
**3) Cover need** → `(ADS×C×uplift)+SS(C)` = `{uplifted_cover:g}+{ss_c:g}` → AI cover `{target:g}`  
**4) Min / Max (Wecomm)** → min=`{min_oh:g}` (`{min_src}`), max=`{max_oh:g or 'none'}`, below min=`{str(below_min).upper()}`  
**5) Desired** → `max(cover, min)` then ≤ max → **`{desired:g}`**  
**6) Stock at arrival** → `max(0, OH−ADS×L)` = **`{at_arrival:g}`**  
**7) Order** → `desired − at arrival` = raw `{raw:g}` → pack (≥80% of {pack}) → **qty={qty:g}**, **cases={cases:g}**  
**8) SS split** → SS(L)=`{ss_l:g}` for ROP only; SS(C)=`{ss_c:g}` in cover/order · ADS cover base=`{ads_cover:g}`
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
            "Keep ‘Generate justifications’ off for demos, then retry."
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
    page_title="Reorder AI Demo",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
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
    st.title("Reorder AI")
    st.caption("Demo UI over FastAPI endpoints")

    st.session_state.api_base = st.text_input(
        "API base URL",
        value=st.session_state.api_base,
        help="Use 8001 if that is where the updated server is running.",
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
    st.markdown("**Endpoints used**")
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


# ── Tabs ─────────────────────────────────────────────────────────────────────

tab_order, tab_chat, tab_run, tab_tools = st.tabs(
    ["1 · Detect order", "2 · Chatbot", "3 · Saved run", "4 · Tools list"]
)


# ── Tab 1: Detect order ──────────────────────────────────────────────────────

with tab_order:
    st.subheader("W-1 Detect order workflow")
    st.write(
        "Pick a vendor, set **lead time (L)** and **days to cover (C)**. "
        "Order is sized for **X = L + C**: sales during lead + stock to cover after arrival. "
        "ML P50/P90 (+ uplift) apply to that full window. "
        "A case is suggested only if need ≥ **80%** of pack."
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

    opts = st.columns(3)
    with opts[0]:
        include_zero = st.checkbox(
            "Include full catalog (SKIP / already covered)",
            value=False,
            help="Default shows ORDER + WATCH only. Turn on to audit every SKU.",
        )
    with opts[1]:
        gen_just = st.checkbox("Generate justifications (slower)", value=False)
    with opts[2]:
        st.write("")

    if st.button("Run detect-order", type="primary", use_container_width=True):
        with st.spinner("Calling POST /api/detect-order …"):
            body = {
                "vendor_id": str(vendor.get("vendor_id")),
                "vendor_name": str(vendor.get("vendor_name")),
                "lead_time_days": int(lead),
                "time_to_cover_days": int(cover),
                "include_zero_orders": bool(include_zero),
                "generate_justification": bool(gen_just),
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
        st.divider()
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Catalog", result.get("catalog_item_count") or result.get("item_count") or 0)
        m2.metric("Order lines", result.get("order_line_count", 0))
        m3.metric("Units", result.get("total_units_to_order", 0))
        m4.metric("Cases", result.get("total_cases_to_order", 0))
        m5.metric("DB", result.get("db_mode", "—"))
        m6.metric("Forecast", result.get("forecast_mode", "—"))
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
                f"+ C={result.get('time_to_cover_days', cover)}). Justification is not shown in the table."
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
