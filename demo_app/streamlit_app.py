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

DEFAULT_API = "http://127.0.0.1:8001"
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
        include_zero = st.checkbox("Include zero-order lines", value=False)
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
            df = pd.DataFrame(items)
            show_cols = [
                c
                for c in [
                    "description",
                    "available_stock",
                    "ads",
                    "lead_demand_ads",
                    "cover_demand_ads",
                    "safety_stock",
                    "reorder_point",
                    "below_reorder_point",
                    "ads_cover_qty",
                    "uplift_multiplier",
                    "p50_demand",
                    "p90_demand",
                    "ai_target_qty",
                    "qty_to_order",
                    "cases_to_order",
                    "box_qty",
                    "last_pallet_qty",
                    "demand_class",
                    "upc",
                    "justification",
                ]
                if c in df.columns
            ]
            st.dataframe(
                df[show_cols],
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

            with st.expander("Line detail / justification"):
                labels = [
                    f"{it.get('description')} → order {it.get('qty_to_order')}"
                    for it in items
                ]
                pick = st.selectbox("Item", labels, key="detail_pick")
                it = items[labels.index(pick)]
                left, right = st.columns(2)
                with left:
                    st.json(
                        {
                            "item_id": it.get("item_id"),
                            "upc": it.get("upc"),
                            "available_stock": it.get("available_stock"),
                            "p50_demand": it.get("p50_demand"),
                            "p90_demand": it.get("p90_demand"),
                            "qty_to_order": it.get("qty_to_order"),
                            "last_pallet_qty": it.get("last_pallet_qty"),
                            "box_qty": it.get("box_qty"),
                            "expiry_capped": it.get("expiry_capped"),
                        }
                    )
                with right:
                    st.markdown("**Justification**")
                    st.write(it.get("justification") or "_none_")
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
                st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)


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
**Demo flow for TL**
1. Sidebar → confirm Health + DB  
2. **Detect order** → Load vendors → pick vendor → set L / C → Run  
3. Review order table (`last_pallet_qty`, P50/P90, qty)  
4. **Chatbot** → ask why / expiry / summary on that `run_id`  
5. **Saved run** → reload any past `run_id`
"""
    )
