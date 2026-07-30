"""Vendor reorder dashboard — pick a vendor, schedule or forecast cover, order list."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.dashboard.cache_utils import clear_all_dashboard_caches
from app.dashboard.pos_data_service import (
    build_enriched_sales_cached,
    sales_date_span,
)
from app.dashboard.pos_reorder_math import _daily_totals_with_zeros
from app.dashboard.theme import apply_metric_box_theme, sched_info_card, vendor_hero
from app.dashboard.vendor_catalog_loader import (
    DEFAULT_NO_SCHEDULE_COVER_DAYS,
    get_all_store_vendors,
    load_delivery_schedule,
    resolve_planning_cover_days,
)
from app.dashboard.vendor_reorder_service import (
    FORECAST_COMPARE_DAYS,
    _build_sales_index,
    _daily_sales_for_upc,
    build_tracked_vendor_reorder_plan,
    build_vendor_order_view,
    trend_by_day_type,
    trend_by_festival,
    trend_by_weather,
)
from v2.analytics.dashboard_constants import DASHBOARD_NOTE

PLOTLY_LAYOUT = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#c9d1d9"),
    colorway=["#3fb950", "#58a6ff", "#d29922", "#f85149"],
)

# Bump when inventory / reorder logic changes (invalidates Streamlit cache)
_VENDOR_VIEW_CACHE_VERSION = 63


def _sale_day_breakdown(upc: str, ads_window_days: int) -> dict:
    """Past-sales day list for the Product math walkthrough (lookback window)."""
    empty = {
        "sale_days": 0,
        "zero_days": int(ads_window_days),
        "qty_list": [],
        "qty_text": "no sales in lookback",
        "demand_std": 0.0,
    }
    try:
        enriched = build_enriched_sales_cached()
        if enriched.empty:
            return empty
        sales_index = _build_sales_index(enriched)
        daily = _daily_sales_for_upc(enriched, str(upc).strip(), sales_index)
        as_of = pd.to_datetime(enriched["date"], errors="coerce").max()
        filled, _total, _start, _ref = _daily_totals_with_zeros(
            daily,
            ads_window_days=int(ads_window_days),
            as_of_date=as_of,
        )
        if filled.empty:
            return empty
        nonzero = filled[filled > 0]
        qty_list = [int(round(float(x))) for x in nonzero.tolist()]
        zero_days = int((filled == 0).sum())
        sale_days = int(len(nonzero))
        std = float(filled.std(ddof=1)) if len(filled) > 1 else 0.0
        if qty_list:
            qty_text = " + ".join(str(q) for q in qty_list) + f" = {sum(qty_list)}"
        else:
            qty_text = "no sales in lookback"
        return {
            "sale_days": sale_days,
            "zero_days": zero_days,
            "qty_list": qty_list,
            "qty_text": qty_text,
            "demand_std": round(std, 2),
        }
    except Exception:
        return empty


def _pick_demo_example_row(reorder_df: pd.DataFrame) -> pd.Series | None:
    """Choose a clear walkthrough row from this vendor's current order list."""
    if reorder_df is None or reorder_df.empty:
        return None
    df = reorder_df[reorder_df["order_qty"] > 0].copy()
    if df.empty:
        df = reorder_df.copy()
    if df.empty:
        return None

    def _score(r: pd.Series) -> float:
        stock = float(r.get("current_stock") or 0)
        ai = float(r.get("ai_min") or 0)
        need = float(r.get("units_needed") or 0)
        pack = float(r.get("pack_size") or 1)
        order = float(r.get("order_qty") or 0)
        ml = float(r.get("ml_forecast_demand") or 0)
        score = 0.0
        if need > 0:
            score += 40
        if ai > stock:
            score += 20
        if pack > 1:
            score += 25
        if order > need:
            score += 15  # uplift or pack story is visible
        if ml > 0:
            score += 10
        if stock < 0:
            score += 8
        # Prefer moderate orders that are easy to explain out loud
        if 6 <= order <= 48:
            score += 12
        return score

    df = df.copy()
    df["_demo_score"] = df.apply(_score, axis=1)
    df = df.sort_values(["_demo_score", "order_qty"], ascending=[False, False])
    return df.iloc[0]


@st.cache_data(ttl=300)
def _vendor_options() -> pd.DataFrame:
    schedule = load_delivery_schedule()
    rows = []
    for v in get_all_store_vendors():
        name = v["inventory_names"][0]
        _, sched = resolve_planning_cover_days(name, schedule)
        rows.append(
            {
                "key": v["key"],
                "name": name,
                "scheduled": bool(sched.get("has_known_schedule")),
                "lead_days": int(sched.get("lead_time_days", DEFAULT_NO_SCHEDULE_COVER_DAYS)),
                "min_days_cover": str(sched.get("min_days_cover", "") or ""),
                "order_frequency": str(sched.get("order_frequency", "") or ""),
                "catalog_file": v.get("catalog_file", ""),
            }
        )
    df = pd.DataFrame(rows)
    df = df.sort_values(["scheduled", "name"], ascending=[False, True]).reset_index(drop=True)
    return df


def _parse_min_cover_days(text: str, fallback: int) -> int:
    """Parse Excel MIN QUANTITY MAINTAIN like '15 DAYS' / '1 MONTH' into cover days."""
    t = str(text or "").strip().upper()
    if not t:
        return fallback
    if "MONTH" in t:
        m = re.search(r"(\d+)", t)
        months = int(m.group(1)) if m else 1
        return max(months * 30, fallback)
    m = re.search(r"(\d+)\s*DAY", t)
    if m:
        return max(int(m.group(1)), 1)
    m = re.search(r"(\d+)", t)
    if m:
        return max(int(m.group(1)), 1)
    return fallback


def _vendor_label(row: pd.Series) -> str:
    tag = "📅 Schedule" if row["scheduled"] else "📊 Forecast"
    return f"{row['name']}  ·  {tag}"


@st.cache_data(ttl=300, show_spinner="Building order list for vendor…")
def _cached_vendor_view(
    vendor_key: str,
    cover_days: int,
    ads_window: int,
    active_only: bool,
    use_uplift: bool,
    use_ml: bool,
    days_to_cover: int,
    vendor_lead_days: int,
    strategy_mode: str,
    use_news: bool,
    _cache_version: int = _VENDOR_VIEW_CACHE_VERSION,
) -> dict:
    return build_vendor_order_view(
        vendor_key,
        cover_days=cover_days,
        ads_window_days=ads_window,
        active_only=active_only,
        use_future_uplift=use_uplift,
        use_ml_forecast=use_ml,
        days_to_cover=days_to_cover,
        vendor_lead_days=vendor_lead_days,
        strategy_mode=strategy_mode,
        use_news_signals=use_news,
    )


@st.cache_data(ttl=300, show_spinner="Loading store-wide summary…")
def _cached_full_plan(
    ads_window: int,
    active_only: bool,
    use_uplift: bool,
    use_ml: bool,
    cover_days: int,
    _cache_version: int = _VENDOR_VIEW_CACHE_VERSION,
) -> dict:
    return build_tracked_vendor_reorder_plan(
        ads_window_days=ads_window,
        active_only=active_only,
        use_future_uplift=use_uplift,
        use_ml_forecast=use_ml,
        overall_cover_days=cover_days,
        force_cover_for_unscheduled=True,
        include_all_store_vendors=False,
    )


def _fig_bar(df: pd.DataFrame, x: str, y: str, title: str, color: str | None = None) -> go.Figure:
    if color and color in df.columns:
        fig = px.bar(df, x=x, y=y, color=color, title=title)
    else:
        fig = px.bar(df, x=x, y=y, title=title, color_discrete_sequence=["#3fb950"])
    fig.update_layout(**PLOTLY_LAYOUT, showlegend=False)
    return fig


def _render_prophet_hybrid_tab(
    *,
    products: pd.DataFrame,
    reorder: pd.DataFrame,
    vendor_name: str,
    vendor_key: str,
    lead_days: int,
    cover_days: int,
) -> None:
    """Pick one SKU → Prophet baseline → LightGBM correction → hybrid order."""
    st.markdown("### Prophet → LightGBM residual (one product)")
    st.caption(
        "Prophet = macro demand. LightGBM = correction from manual order/receive pattern. "
        "Expiry is not used. Runs only when you click the button (not for all 3000 SKUs)."
    )

    pool = products.copy() if products is not None and not products.empty else reorder.copy()
    if pool is None or pool.empty or "description" not in pool.columns:
        st.info("No products loaded for this vendor yet.")
        return

    names = pool["description"].astype(str).tolist()
    pick = st.selectbox(
        "Select product",
        options=names,
        key=f"prophet_hybrid_pick_{vendor_key}",
        help="Choose any catalog product for this vendor.",
    )
    row = pool[pool["description"].astype(str) == pick].iloc[0]
    upc = str(row.get("upc") or "").strip()
    stock = float(row.get("current_stock") or 0)
    # Prefer non-negative stock for order math display
    stock_for_order = max(stock, 0.0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Lead Time L", f"{lead_days}d")
    c2.metric("Days to Cover C", f"{cover_days}d")
    c3.metric("Window L+C", f"{lead_days + cover_days}d")
    c4.metric("On hand", f"{stock:g}")

    if st.button(
        "Run Prophet hybrid for this product",
        type="primary",
        key=f"run_prophet_hybrid_{vendor_key}",
        use_container_width=True,
    ):
        with st.spinner("Fitting Prophet + LightGBM residual for this SKU…"):
            from app.dashboard.hybrid_sku_service import run_hybrid_for_selected_product

            result = run_hybrid_for_selected_product(
                upc=upc,
                product_name=str(pick),
                vendor_name=vendor_name,
                physical_stock=stock_for_order,
                lead_time_days=lead_days,
                days_to_cover=cover_days,
            )
        st.session_state[f"prophet_hybrid_result_{vendor_key}"] = result

    result = st.session_state.get(f"prophet_hybrid_result_{vendor_key}")
    if not result:
        st.info("Select a product, then click **Run Prophet hybrid for this product**.")
        return
    if not result.get("ok"):
        st.error(result.get("error") or "Hybrid run failed.")
        return

    story = result["story"]
    st.success(
        f"**{result['product_name']}** · sales {result['sales_start']} → {result['sales_end']} "
        f"({result['sales_days']} days) · {result['order_log_note']}"
    )

    st.markdown(
        f"""
#### Story for this product

1. **Prophet said** you need about **{story['prophet_said']}** units over the next **{story['L'] + story['C']}** days  
2. **LightGBM corrected** that by **{story['correction_total']:+}** (order-pattern residual)  
3. **Hybrid expected demand** = **{story['hybrid_total']}**  
4. **Safety stock** (Prophet upper − yhat) = **{story['safety_stock']}**  
5. **ROP** = hybrid over L (**{story['L']}d**) + safety = **{story['rop']}**  
6. On hand **{story['stock']}** → **Recommended order = {story['order_qty']}**
"""
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Prophet (L+C)", f"{story['prophet_said']}")
    m2.metric("Correction", f"{story['correction_total']:+}")
    m3.metric("Hybrid (L+C)", f"{story['hybrid_total']}")
    m4.metric("Order qty", f"{story['order_qty']}")

    st.markdown("#### Future days — what Prophet said vs what got corrected")
    fut = result["future"]
    st.dataframe(fut, use_container_width=True, hide_index=True)

    if not fut.empty and "prophet_yhat" in fut.columns:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=fut["ds"],
                y=fut["prophet_yhat"],
                name="Prophet yhat",
                mode="lines+markers",
                line=dict(color="#58a6ff"),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=fut["ds"],
                y=fut["hybrid_demand"],
                name="Hybrid (Prophet + correction)",
                mode="lines+markers",
                line=dict(color="#3fb950"),
            )
        )
        if "lgbm_correction" in fut.columns:
            fig.add_trace(
                go.Bar(
                    x=fut["ds"],
                    y=fut["lgbm_correction"],
                    name="LightGBM correction",
                    marker_color="#d29922",
                    opacity=0.55,
                    yaxis="y2",
                )
            )
        fig.update_layout(
            **PLOTLY_LAYOUT,
            title="Prophet vs Hybrid demand",
            yaxis=dict(title="Units / day"),
            yaxis2=dict(title="Correction", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h"),
            barmode="relative",
        )
        st.plotly_chart(fig, use_container_width=True)

    with st.expander("Recent history — Actual vs Prophet vs Hybrid", expanded=False):
        st.dataframe(result["history_tail"], use_container_width=True, hide_index=True)
        mets = result.get("metrics") or {}
        if mets:
            st.caption(
                f"Fit: Prophet MAE={mets.get('prophet_mae', 0):.2f} · "
                f"Hybrid MAE={mets.get('hybrid_mae', 0):.2f} · "
                f"Residual MAE={mets.get('residual_mae', 0):.2f}"
            )


def render_vendor_reorder_page() -> None:
    apply_metric_box_theme()
    st.markdown("## Window 1 — Vendor Intelligent Reorder")
    st.caption(
        f"Lead Time + Days to Cover horizon · Hybrid ML · Okemos news. {DASHBOARD_NOTE}"
    )

    vendor_df = _vendor_options()
    if vendor_df.empty:
        st.error("No vendors found in data/vendors/.")
        return

    with st.sidebar:
        st.markdown("### ⚙️ Settings")
        first_sale, last_sale, max_lookback = sales_date_span()
        max_lookback = max(int(max_lookback or 30), 30)
        use_max_lookback = st.toggle(
            "Max lookback (from first sales date)",
            value=False,
            help=(
                "On: ADS / sold use the full sales history from the first date on file "
                f"({first_sale} → {last_sale}, {max_lookback} days). "
                "Off: choose a shorter lookback with the slider."
            ),
        )
        if use_max_lookback:
            ads_window = int(max_lookback)
            st.info(
                f"**Full history:** {first_sale} → {last_sale} "
                f"(**{ads_window} days**)"
            )
        else:
            default_lb = 30 if max_lookback >= 30 else max_lookback
            ads_window = st.slider(
                "Sales lookback (days)",
                min_value=14,
                max_value=int(max_lookback),
                value=min(default_lb, max_lookback),
                help=(
                    f"Past days used for Sold / ADS / AI min. "
                    f"Max = full history ({max_lookback}d from {first_sale}). "
                    "Toggle **Max lookback** to use every day from the first sale date."
                ),
            )
        active_only = st.checkbox(
            "Hide POS-inactive flags",
            value=False,
            help=(
                "Off (default): list everything from inventory count — team decides. "
                "On: hide SKUs marked inactive in POS."
            ),
        )
        use_uplift = st.checkbox(
            "Festival / holiday / weekend uplift (per item)",
            value=True,
            help=(
                "ON: each product gets its own uplift from THAT product's Sat/Sun/festival sales "
                "vs its weekday sales. Weather never changes order qty."
            ),
        )
        # Window 1 always uses Hybrid ML: max(formula need, horizon forecast − stock)
        use_ml = True
        strategy_mode = "Hybrid ML"
        st.info(
            "**Hybrid ML** (always on): need = max(formula AI Min, ML forecast − stock), "
            "then news / case rounding."
        )
        use_news = st.checkbox(
            "Okemos / 48864 regional news signals",
            value=True,
            help="Soft demand adjustments for Mid-Michigan only (never USA-wide).",
        )
        if st.button("Refresh data", use_container_width=True, type="primary"):
            clear_all_dashboard_caches()
            st.rerun()
        st.divider()
        n_sched = int(vendor_df["scheduled"].sum())
        st.markdown(f"**{len(vendor_df)}** vendors · **{n_sched}** with delivery schedule")
        if first_sale and last_sale:
            st.caption(f"Sales on file: **{first_sale}** → **{last_sale}**")
        heat_path = Path(__file__).resolve().parents[2] / "outputs" / "analytics" / "candidate_feature_correlation_heatmap.html"
        if heat_path.exists():
            st.caption(f"Candidate heatmap: `{heat_path.name}`")
    # ── Vendor picker (main) ──
    st.markdown("#### Select vendor")
    vendor_idx = st.selectbox(
        "Vendor",
        range(len(vendor_df)),
        format_func=lambda i: _vendor_label(vendor_df.iloc[i]),
        label_visibility="collapsed",
    )
    row = vendor_df.iloc[vendor_idx]
    vendor_key = row["key"]
    vendor_name = row["name"]
    is_scheduled = bool(row["scheduled"])

    # Defaults: cover = days until next order; lead = Excel transit (or 7 if unknown).
    schedule_lead = int(row.get("lead_days") or DEFAULT_NO_SCHEDULE_COVER_DAYS)
    min_cover = _parse_min_cover_days(str(row.get("min_days_cover", "")), schedule_lead)
    is_hos = str(vendor_key).upper() == "HOS" or "HOS" in str(vendor_name).upper()
    if is_hos:
        default_cover = 14  # biweekly HOS orders (~2× per month)
        cover_hint = "HOS order cycle (~14 days / 2× per month)"
    elif is_scheduled:
        default_cover = max(min_cover, 1)
        freq = str(row.get("order_frequency") or "").strip() or "schedule"
        cover_hint = f"{freq} · maintain {row.get('min_days_cover') or default_cover}"
    else:
        default_cover = DEFAULT_NO_SCHEDULE_COVER_DAYS
        cover_hint = f"default {default_cover} days"
    default_lead = max(int(schedule_lead), 1)

    if is_scheduled:
        st.markdown(
            '<span class="badge-scheduled">📅 Scheduled vendor — cut-off/delivery from Excel. '
            "Use the Cover / Lead toggles below for every vendor.</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<span class="badge-forecast">No delivery schedule on file — set Cover / Lead below. '
            "AI builds the order from past sales + inventory.</span>",
            unsafe_allow_html=True,
        )

    st.markdown("#### Window 1 inputs — Lead Time + Days to Cover")
    t1, t2 = st.columns(2)
    with t1:
        cover_days = st.slider(
            "Days to Cover (after arrival)",
            min_value=1,
            max_value=60,
            value=int(st.session_state.get(f"manual_cover_{vendor_key}", default_cover)),
            key=f"manual_cover_{vendor_key}",
            help=f"How many days the new pallet should cover after delivery. Default: {cover_hint}.",
        )
    with t2:
        lead_days = st.slider(
            "Vendor Lead Time (days)",
            min_value=0,
            max_value=60,
            value=int(st.session_state.get(f"manual_lead_{vendor_key}", default_lead)),
            key=f"manual_lead_{vendor_key}",
            help=f"Days until delivery. Excel default: {default_lead} days.",
        )
    use_cover = True
    use_lead = True

    planning_days = int(cover_days) + int(lead_days)
    if planning_days < 1:
        st.warning("Set Lead Time and/or Days to Cover so AI can plan an order window.")
        planning_days = int(default_cover)
        cover_days = planning_days
        lead_days = 0

    parts = [f"lead **{lead_days}d**", f"cover **{cover_days}d**"]
    st.info(
        f"**Forecast horizon = Lead + Cover = {planning_days} days** for **{vendor_name}** "
        f"({' + '.join(parts)}). Strategy: **{strategy_mode}**. Lookback: **{ads_window}d**."
    )

    try:
        pack_bump = int(st.session_state.get("_pack_override_bump", 0))
        # bump cache version via unused internal version field
        _ = pack_bump
        view = _cached_vendor_view(
            vendor_key,
            planning_days,
            ads_window,
            active_only,
            use_uplift,
            use_ml,
            int(cover_days),
            int(lead_days),
            "hybrid",
            use_news,
            _VENDOR_VIEW_CACHE_VERSION + pack_bump,
        )
    except Exception as exc:
        st.error(f"Could not load vendor: {exc}")
        return

    if view.get("error"):
        st.error(view["error"])
        return

    sched = view["schedule"]
    reorder = view["reorder_lines"]
    products = view["products"]
    horizon = view.get("horizon_table", pd.DataFrame())
    forecast_compare = view.get("forecast_compare_table", pd.DataFrame())
    start, end = view["sales_date_range"]
    effective_cover = int(planning_days)
    cover_on_txt = f"cover {cover_days}d"
    lead_on_txt = f"lead {lead_days}d"

    badge = (
        f'<span class="badge-scheduled">AI planning {effective_cover}d'
        f" · {cover_on_txt} · {lead_on_txt}"
        f"{' · schedule on file' if is_scheduled else ''}</span>"
    )
    vendor_hero(
        vendor_name,
        f"Catalog: `{row['catalog_file']}` · Sales {start or '—'} → {end or '—'}",
        badge,
    )

    # Schedule info cards
    s1, s2, s3, s4 = st.columns(4)
    with s1:
        if is_scheduled:
            sched_info_card("Order cut-off", str(sched.get("order_cutoff", "—")))
        else:
            sched_info_card("Mode", "AI cover / lead")
    with s2:
        if is_scheduled:
            sched_info_card("Delivery days", str(sched.get("delivery_days", "—")))
        else:
            sched_info_card("Cover days", f"{cover_days if use_cover else 0} ({'on' if use_cover else 'off'})")
    with s3:
        sched_info_card(
            "Lead days",
            f"{lead_days if use_lead else 0} ({'on' if use_lead else 'off'})"
            + (f" · Excel {sched.get('lead_time_days', '—')}" if is_scheduled else ""),
        )
    with s4:
        ml_horizon = 7 if effective_cover <= 7 else 14 if effective_cover <= 14 else 21 if effective_cover <= 21 else 30
        sched_info_card("AI planning window", f"{effective_cover}d · ML {ml_horizon}d")

    # Metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("SKUs in catalog", f"{len(products):,}")
    m2.metric("Need reorder", f"{len(reorder):,}")
    m3.metric("Units to order", f"{int(reorder['order_qty'].sum()) if not reorder.empty else 0:,}")
    m4.metric("Order lines", f"{len(reorder):,}")
    m5.metric("Est. cost", f"${reorder['est_order_cost'].dropna().sum():,.0f}" if not reorder.empty else "$0")

    tab_order, tab_horizon, tab_prophet, tab_all, tab_sched, tab_trends, tab_future, tab_explain = st.tabs(
        [
            "Order list",
            "7 / 14 / 25d forecast",
            "Prophet hybrid",
            "All vendors",
            "All schedules",
            "Trends",
            "Future outlook",
            "AI explain",
        ]
    )

    with tab_order:
        if len(products) == 0:
            st.warning(
                f"No products found for **{vendor_name}**. "
                "Inventory vendor names may be stale — click **🔄 Refresh data** in the sidebar, "
                "or check `data/inventory/current inventory count.csv`."
            )
        elif reorder.empty:
            st.success("All products above minimum — nothing to order right now for this vendor.")
        else:
            sold_col = f"Sold last {ads_window}d"
            uplift_expl = view.get("uplift_explanation") or {}
            uplift_x = float(uplift_expl.get("uplift_factor", view.get("horizon_uplift") or 1.0))
            driver_day = uplift_expl.get("driver_day_name") or "upcoming busy day"
            driver_date = uplift_expl.get("driver_date") or "soon"
            driver_type = uplift_expl.get("driver_day_type") or "Weekend"

            # Dynamic example from THIS vendor — pick any product and see full math
            ml_model_days = (
                7 if effective_cover <= 7 else 14 if effective_cover <= 14 else 21 if effective_cover <= 21 else 30
            )
            mode_label = (
                f"Planning = {effective_cover}d ({cover_on_txt} + {lead_on_txt})"
            )
            # Prefer order lines, but allow picking any catalog product for this vendor
            example_pool = products.copy() if not products.empty else reorder.copy()
            if example_pool.empty:
                example_pool = reorder.copy()
            preferred = (
                example_pool[example_pool["order_qty"] > 0]
                if "order_qty" in example_pool.columns
                else example_pool
            )
            if preferred.empty:
                preferred = example_pool
            default_row = _pick_demo_example_row(preferred)
            example_names = example_pool["description"].astype(str).tolist()
            default_name = (
                str(default_row["description"])
                if default_row is not None
                else (example_names[0] if example_names else "")
            )
            pick_idx = 0
            if default_name in example_names:
                pick_idx = example_names.index(default_name)
            example_name = st.selectbox(
                f"Example product from {vendor_name}",
                options=example_names,
                index=min(pick_idx, max(len(example_names) - 1, 0)),
                key=f"demo_example_{vendor_key}_{effective_cover}_{ads_window}",
                help=(
                    "Select any product (e.g. CADBURY DAIRY MILK) to see "
                    "Past sales → Base need → Uplift → Case math with every number explained."
                ),
            )
            demo_row = example_pool[example_pool["description"].astype(str) == example_name].iloc[0]

            d_upc = str(demo_row.get("upc") or "")
            d_stock = float(demo_row.get("current_stock") or 0)
            d_stock_need = float(demo_row.get("stock_for_reorder") or max(d_stock, 0))
            d_ai = int(demo_row.get("ai_min") or 0)
            d_order = int(demo_row.get("order_qty") or 0)
            d_pack = max(int(demo_row.get("pack_size") or 1), 1)
            d_cases = float(demo_row.get("cases_to_order") or 0)
            d_ml = float(demo_row.get("ml_forecast_demand") or 0)
            d_ads = float(demo_row.get("ads") or 0)
            d_sold = int(demo_row.get("sold_in_lookback") or 0)
            d_uplift = float(demo_row.get("horizon_uplift") or uplift_x)
            d_source = str(demo_row.get("reorder_reason") or "formula")
            d_name = str(demo_row.get("description") or "Example product")
            d_ss = int(demo_row.get("safety_stock") or 0)
            d_std = float(demo_row.get("demand_std") or 0.0)
            d_plan = int(demo_row.get("planning_cover_days") or effective_cover)
            d_ltd = int(demo_row.get("lead_time_demand") or round(d_ads * d_plan))
            d_inv_max = float(demo_row.get("invoice_max_units") or 0)
            d_inv_med = float(demo_row.get("invoice_median_units") or 0)
            d_inv_n = int(demo_row.get("invoice_order_count") or 0)
            d_inv_note = str(demo_row.get("invoice_cap_note") or "").strip()
            d_formula = str(
                demo_row.get("formula_breakdown")
                or f"({d_ads:.1f} × {d_plan}) + {d_ss} = {d_ai}"
            )
            if "formula_raw_need" in demo_row.index and pd.notna(demo_row.get("formula_raw_need")):
                formula_need = int(demo_row.get("formula_raw_need") or 0)
            else:
                formula_need = max(0, int(round(d_ai - d_stock_need)))
            if "ml_need" in demo_row.index and pd.notna(demo_row.get("ml_need")):
                ml_need = int(demo_row.get("ml_need") or 0)
            else:
                ml_need = max(0, int(round(d_ml - d_stock_need))) if d_ml > 0 else 0
            need_used = max(formula_need, ml_need)
            after_uplift = int(demo_row.get("units_after_uplift") or round(need_used * d_uplift))
            if "uplift_extra_units" in demo_row.index and pd.notna(demo_row.get("uplift_extra_units")):
                uplift_extra = int(demo_row.get("uplift_extra_units") or 0)
            else:
                uplift_extra = max(0, after_uplift - need_used)
            sale_br = _sale_day_breakdown(d_upc, ads_window)
            if d_std <= 0 and sale_br.get("demand_std"):
                d_std = float(sale_br["demand_std"])
            import math as _math
            ss_raw = (
                round(1.65 * d_std * _math.sqrt(max(d_plan, 1)), 2) if d_std > 0 else 0.0
            )
            d_neg = int(demo_row.get("negative_sold_units") or 0)
            pos_sold_only = max(0, d_sold - d_neg)
            day_sum = int(sum(sale_br.get("qty_list") or []))
            # Prefer walkthrough day-sum when it matches POS; else show POS total
            shown_pos = day_sum if day_sum > 0 else pos_sold_only


            st.markdown(f"### Product\n\n**{d_name}**")
            st.caption(
                f"{vendor_name} · {mode_label} · lookback {ads_window}d · Source = {d_source}"
            )

            st.success(
                f"**{d_sold}** = past sales  →  "
                f"**{formula_need}** = Base need "
                f"({d_ltd} expected + {d_ss} safety"
                f"{'' if d_stock_need <= 0 else f' − {d_stock_need:g} stock'})  →  "
                f"**{after_uplift}** = after uplift  →  "
                f"**{d_order}** = units needed (order this many units)"
            )

            c_a, c_b, c_c, c_d = st.columns(4)
            c_a.metric("Past sales", f"{d_sold}", help=f"Sold in last {ads_window} days")
            c_b.metric(
                "Base need",
                f"{formula_need}",
                help=f"{d_ltd} expected + {d_ss} safety − stock",
            )
            c_c.metric(
                "After uplift",
                f"{after_uplift}",
                help=f"Base {need_used} + extra {uplift_extra}",
            )
            c_d.metric(
                "Units needed",
                f"{d_order}",
                help=f"{d_cases:g} case(s) × {d_pack} units (50% fill rule)",
            )

            st.markdown(
                f"""
### How Base need is calculated

Your planning window = Cover **{cover_days if use_cover else 0}** + Lead **{lead_days if use_lead else 0}** = **{d_plan} days**

#### 1. ADS (average per day)
**{d_sold} sold ÷ {ads_window} days = {d_ads:.2f} per day**

Past sales **{d_sold}** is only history. It is **not** the order quantity and **not** Base need.

- POS sale days sum ≈ **{shown_pos}**
{f"- Plus **{d_neg}** from negative on-hand count (sold but receive not added) → Past sales **{d_sold}**" if d_neg > 0 else ""}

#### 2. Expected sales in next {d_plan} days
**{d_ads:.2f} × {d_plan} ≈ {d_ltd}**
← “if it keeps selling like this, you’ll sell about **{d_ltd}** in **{d_plan}** days”

#### 3. Safety stock (the big jump)
Sales were not smooth. Real history in the last **{ads_window}** days:

- Sale days with quantity: **{sale_br['sale_days']}** day(s) → `{sale_br['qty_text']}`
- Zero-sale days: **{sale_br['zero_days']}** day(s)

Day-to-day variation (std) = **{d_std:.2f}**

Safety formula = `1.65 × std × √(planning days)`
= `1.65 × {d_std:.2f} × √{d_plan} ≈ {ss_raw}`
then capped so safety cannot exceed expected sales (**{d_ltd}**) → **Safety = {d_ss}**

Because sales are bursty, AI adds this buffer.

#### 4. AI Min
**{d_ltd} (expected) + {d_ss} (safety) = {d_ai}**
Exact formula from engine: `{d_formula}`

#### 5. Base need (stock = {d_stock_need:g})
**{d_ai} − {d_stock_need:g} = {formula_need}**
← **this is Base need**

So Base need **{formula_need}** = expected **{d_ltd}** + safety **{d_ss}** − stock.
It is **not** “past sales {d_sold}”. If those numbers look close (e.g. both near 39), that is coincidence.

---

### Next steps after Base need

#### ML check
ML forecast for ~{ml_model_days}d window = **{d_ml:g}** → ML need = **{ml_need}**
AI uses **max(Base {formula_need}, ML {ml_need}) = {need_used}**

#### Uplift ({driver_day} {driver_date}, {driver_type}) — **this product only**
**{need_used} × {d_uplift:.3f} ≈ {after_uplift}**
Extra from uplift = **{uplift_extra}** (already inside {after_uplift} — do **not** add again)

This factor comes from **this item's** Sat/Sun/festival sales vs its weekday sales — not a store-wide boost.

#### Units to order
**Units in 1 case = {d_pack}** (from inventory or past invoices)

Need after uplift/cap is rounded to cases with the **50% rule**:
next case only if leftover need ≥ half a case.

**Cases to order = {d_cases:g}** → **Units needed = {d_order}**

#### Past invoices (shop / shelf size)
Back-team history for this item: **{d_inv_n}** past order(s) · median **{d_inv_med:g}** · max **{d_inv_max:g}** units.
{f"**{d_inv_note}**" if d_inv_note else "AI suggestion is within historical order sizes — no shop-size cap applied."}
"""
            )

            with st.expander("More simple explanation", expanded=False):
                st.markdown(
                    f"""
**Why 1.65 for safety stock?**
95% service level (standard). 90%→1.28, **95%→1.65**, 99%→2.33.

**Why uplift ~{uplift_x:.3f} now?**
Weekends sell about 30% more than a normal day in store history.
Driver: **{driver_day} ({driver_date}, {driver_type})**. Weather is not used on orders.

**Negative stock example:** count shows −45 → add **45 sold** into ADS; on-hand for need = **0**
(forgot to add receive to count; may still hold some stock — we can’t fix that, so don’t order the |−45| as extra shortage).
"""
                )

            # Easy filters for walkthrough
            st.markdown("#### Easy filters")
            f1, f2, f3, f4 = st.columns([2, 1.2, 1.2, 1.2])
            with f1:
                search_q = st.text_input(
                    "Search product",
                    placeholder="e.g. samosa, ashoka, tea",
                    key=f"vendor_search_{vendor_key}",
                )
            with f2:
                source_opts = ["All sources"] + sorted(
                    str(x) for x in reorder["reorder_reason"].dropna().unique()
                )
                source_pick = st.selectbox("Source", source_opts, key=f"vendor_source_{vendor_key}")
            with f3:
                stock_pick = st.selectbox(
                    "Stock",
                    ["All", "Negative only", "Zero only", "Low (at/below AI min)"],
                    key=f"vendor_stock_{vendor_key}",
                )
            with f4:
                quick = st.selectbox(
                    "Quick view",
                    [
                        "All rows",
                        "Uplift raised order",
                        "ML drove order",
                        "Formula floor drove order",
                        "Catalog suggest (1 case)",
                        "Order qty ≥ 12",
                    ],
                    key=f"vendor_quick_{vendor_key}",
                )

            filtered = reorder.copy()
            if search_q.strip():
                q = search_q.strip().upper()
                filtered = filtered[filtered["description"].astype(str).str.upper().str.contains(q, na=False)]
            if source_pick != "All sources":
                filtered = filtered[filtered["reorder_reason"].astype(str) == source_pick]
            if stock_pick == "Negative only":
                filtered = filtered[filtered["current_stock"] < 0]
            elif stock_pick == "Zero only":
                filtered = filtered[filtered["current_stock"] == 0]
            elif stock_pick == "Low (at/below AI min)":
                filtered = filtered[filtered["current_stock"] <= filtered["ai_min"]]
            if quick == "Uplift raised order":
                filtered = filtered[filtered["reorder_reason"].astype(str) == "calendar_uplift"]
            elif quick == "ML drove order":
                filtered = filtered[filtered["reorder_reason"].astype(str) == "ml_forecast"]
            elif quick == "Formula floor drove order":
                filtered = filtered[filtered["reorder_reason"].astype(str) == "formula"]
            elif quick == "Catalog suggest (1 case)":
                filtered = filtered[filtered["reorder_reason"].astype(str) == "catalog_suggest"]
            elif quick == "Order qty ≥ 12":
                filtered = filtered[filtered["order_qty"] >= 12]

            st.caption(
                f"Showing **{len(filtered)}** of {len(reorder)} order lines · "
                f"Lookback **{ads_window}d** · Planning **{effective_cover}d** "
                f"({cover_on_txt}, {lead_on_txt}) · "
                f"Uplift = **per item** (each SKU's own weekend/festival pattern)"
            )

            if filtered.empty:
                st.info("No rows match these filters — clear search / set filters back to All.")
            else:
                simple = filtered.copy()
                sold_units = pd.to_numeric(simple.get("sold_in_lookback"), errors="coerce").fillna(0)
                other_brands = (
                    simple["other_brands_stock"]
                    if "other_brands_stock" in simple.columns
                    else simple.get("similar_alternatives", pd.Series([""] * len(simple)))
                )
                other_brands = other_brands.fillna("").astype(str)
                base_need = (
                    pd.to_numeric(simple.get("units_needed"), errors="coerce")
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                units_after = (
                    pd.to_numeric(
                        simple.get("units_after_uplift", simple.get("units_needed")),
                        errors="coerce",
                    )
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                if "uplift_extra_units" in simple.columns:
                    uplift_extra = pd.to_numeric(simple["uplift_extra_units"], errors="coerce").fillna(0)
                else:
                    uplift_extra = (units_after - base_need).clip(lower=0)
                uplift_extra = uplift_extra.round(0).astype(int)
                expected = (
                    pd.to_numeric(simple.get("lead_time_demand"), errors="coerce")
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                safety = (
                    pd.to_numeric(simple.get("safety_stock"), errors="coerce")
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                on_hand = (
                    pd.to_numeric(simple["current_stock"], errors="coerce")
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                if "need_calc" in simple.columns:
                    need_calc = simple["need_calc"].fillna("").astype(str)
                else:
                    need_rows = []
                    for i in range(len(simple)):
                        parts = [str(int(expected.iloc[i])), str(int(safety.iloc[i]))]
                        u = int(uplift_extra.iloc[i])
                        if u > 0:
                            parts.append(str(u))
                        s = "+".join(parts)
                        oh = max(int(on_hand.iloc[i]), 0)
                        if oh > 0:
                            s = f"{s}-{oh}"
                        tot = int(
                            pd.to_numeric(
                                simple.iloc[i].get("ai_need_units", units_after.iloc[i]),
                                errors="coerce",
                            )
                            or 0
                        )
                        need_rows.append(f"{s}={tot}")
                    need_calc = pd.Series(need_rows, index=simple.index)

                total_need = (
                    pd.to_numeric(
                        simple.get("ai_need_units", simple.get("units_after_uplift")),
                        errors="coerce",
                    )
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                final_units = (
                    pd.to_numeric(simple.get("order_qty"), errors="coerce")
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                pack_units = (
                    pd.to_numeric(simple.get("pack_size"), errors="coerce")
                    .fillna(1)
                    .round(0)
                    .astype(int)
                )
                cases_needed = (
                    pd.to_numeric(simple.get("cases_to_order"), errors="coerce")
                    .fillna(0)
                    .round(2)
                )
                inv_max_cases = (
                    pd.to_numeric(simple.get("invoice_max_cases"), errors="coerce")
                    .fillna(0)
                    .round(0)
                    .astype(int)
                )
                show = pd.DataFrame(
                    {
                        "UPC": simple["upc"].astype(str),
                        "Product": simple["description"].astype(str),
                        "On hand": on_hand,
                        "Forecast demand": pd.to_numeric(
                            simple.get("forecast_demand", simple.get("ml_forecast_demand")),
                            errors="coerce",
                        ).fillna(0).round(1),
                        "Suggested qty": final_units,
                        "Cases": cases_needed,
                        "Demand class": simple.get("demand_class", pd.Series([""] * len(simple))).fillna("").astype(str),
                        "Confidence": (
                            pd.to_numeric(simple.get("confidence_score"), errors="coerce").fillna(0) * 100
                        ).round(0).astype(int).astype(str)
                        + "%",
                        "News": simple.get("news_signal", pd.Series([""] * len(simple))).fillna("").astype(str),
                        "Need calc": need_calc,
                        "Units in 1 case": pack_units,
                        "Other brands on shelf": other_brands,
                    }
                )
                st.caption(
                    "Window 1 **Hybrid ML**: Suggested qty = max(formula AI Min need, ML horizon forecast − stock), "
                    "then news, invoice cap, and 50% case fill."
                )
                st.dataframe(show, use_container_width=True, hide_index=True)
                if st.button(
                    "🔄 Refresh order math",
                    use_container_width=True,
                    key=f"refresh_packs_{vendor_key}",
                ):
                    clear_all_dashboard_caches()
                    st.session_state["_pack_override_bump"] = int(
                        st.session_state.get("_pack_override_bump", 0)
                    ) + 1
                    st.rerun()

                with st.expander("Show detail columns (ADS, AI min, ML, uplift)"):
                    detail = filtered[
                        [
                            c
                            for c in [
                                "description",
                                "current_stock",
                                "sold_in_lookback",
                                "ads",
                                "ai_min",
                                "ml_forecast_demand",
                                "order_qty",
                                "reorder_reason",
                                "formula_breakdown",
                            ]
                            if c in filtered.columns
                        ]
                    ].rename(
                        columns={
                            "description": "Product",
                            "current_stock": "On hand",
                            "sold_in_lookback": sold_col,
                            "ads": f"ADS ({ads_window}d)",
                            "ai_min": "AI min",
                            "ml_forecast_demand": "ML forecast",
                            "order_qty": "Units needed",
                            "reorder_reason": "Source",
                            "formula_breakdown": "Formula",
                        }
                    )
                    st.dataframe(detail, use_container_width=True, hide_index=True)
                buf = io.BytesIO()
                show.to_csv(buf, index=False)
                st.download_button(
                    f"⬇️ Download {vendor_name} order list",
                    buf.getvalue(),
                    file_name=f"order_{vendor_key}_{planning_days}d.csv",
                    use_container_width=True,
                )

    with tab_horizon:
        st.markdown(f"### 7 / 14 / 25 day forecast — **{vendor_name}**")
        st.caption(
            "Separate compare table for this vendor. "
            "**ML** = demand forecast for that horizon · "
            "**Need / Units** = what to order in units (no case rounding) · "
            f"Order list still uses your toggles (**{cover_on_txt}**, **{lead_on_txt}** → **{effective_cover}d**)."
        )
        if forecast_compare.empty:
            st.info("No products available for 7 / 14 / 25 day forecast compare.")
        else:
            fc_cols = [
                "description",
                "current_stock",
                "ads",
            ]
            rename = {
                "description": "Product",
                "current_stock": "On hand",
                "ads": "ADS",
            }
            for d in FORECAST_COMPARE_DAYS:
                for src, label in (
                    (f"ml_fc_{d}d", f"ML {d}d"),
                    (f"need_{d}d", f"Need {d}d"),
                    (f"order_{d}d", f"Units needed {d}d"),
                ):
                    if src in forecast_compare.columns:
                        fc_cols.append(src)
                        rename[src] = label

            # Prefer rows that need something in any horizon
            active = forecast_compare.copy()
            need_any = pd.Series(False, index=active.index)
            for d in FORECAST_COMPARE_DAYS:
                col = f"order_{d}d"
                if col in active.columns:
                    need_any = need_any | (pd.to_numeric(active[col], errors="coerce").fillna(0) > 0)
            if need_any.any():
                active = active.loc[need_any].reset_index(drop=True)

            fc_show = active[[c for c in fc_cols if c in active.columns]].rename(columns=rename)
            st.dataframe(fc_show, use_container_width=True, hide_index=True)

            totals = []
            for d in FORECAST_COMPARE_DAYS:
                oc = f"order_{d}d"
                if oc in active.columns:
                    totals.append(
                        {
                            "days": f"{d}d",
                            "total_units": int(pd.to_numeric(active[oc], errors="coerce").fillna(0).sum()),
                        }
                    )
            if totals:
                tdf = pd.DataFrame(totals)
                m1, m2, m3 = st.columns(3)
                for i, row_t in tdf.iterrows():
                    with (m1, m2, m3)[int(i)]:
                        st.metric(
                            f"{row_t['days']} total",
                            f"{int(row_t['total_units']):,} units",
                        )
                fig = _fig_bar(tdf, "days", "total_units", f"{vendor_name} — units needed by forecast horizon")
                fig.update_traces(marker_line_width=0)
                st.plotly_chart(fig, use_container_width=True)

            buf_fc = io.BytesIO()
            fc_show.to_csv(buf_fc, index=False)
            st.download_button(
                f"⬇️ Download {vendor_name} 7/14/25d forecast table",
                buf_fc.getvalue(),
                file_name=f"forecast_compare_{vendor_key}_7_14_25.csv",
                use_container_width=True,
            )

        if not horizon.empty:
            with st.expander("Also compare 7 / 14 / 21 / 30 day cover", expanded=False):
                hshow = horizon[
                    [
                        c
                        for c in [
                            "description",
                            "current_stock",
                            "ads",
                            "ml_fc_7d",
                            "need_7d",
                            "order_7d",
                            "ml_fc_14d",
                            "need_14d",
                            "order_14d",
                            "ml_fc_21d",
                            "need_21d",
                            "order_21d",
                            "ml_fc_30d",
                            "need_30d",
                            "order_30d",
                        ]
                        if c in horizon.columns
                    ]
                ].rename(
                    columns={
                        "description": "Product",
                        "current_stock": "On hand",
                        "ads": "ADS",
                        "ml_fc_7d": "ML 7d",
                        "need_7d": "Need 7d",
                        "order_7d": "Final 7d",
                        "ml_fc_14d": "ML 14d",
                        "need_14d": "Need 14d",
                        "order_14d": "Final 14d",
                        "ml_fc_21d": "ML 21d",
                        "need_21d": "Need 21d",
                        "order_21d": "Final 21d",
                        "ml_fc_30d": "ML 30d",
                        "need_30d": "Need 30d",
                        "order_30d": "Final 30d",
                    }
                )
                st.dataframe(hshow, use_container_width=True, hide_index=True)

    with tab_prophet:
        _render_prophet_hybrid_tab(
            products=products,
            reorder=reorder,
            vendor_name=vendor_name,
            vendor_key=vendor_key,
            lead_days=int(lead_days),
            cover_days=int(cover_days),
        )

    with tab_all:
        if st.button("Load all-vendor summary", type="secondary"):
            st.session_state["load_all_vendors"] = True
        if st.session_state.get("load_all_vendors"):
            with st.spinner("Loading all vendors…"):
                plan = _cached_full_plan(ads_window, active_only, use_uplift, use_ml, planning_days)
            overall = plan.get("overall_reorder_lines", pd.DataFrame())
            if overall.empty:
                st.success("No reorders store-wide at current settings.")
            else:
                overall_show = overall[
                    [
                        c
                        for c in [
                            "vendor_name",
                            "description",
                            "current_stock",
                            "sold_in_lookback",
                            "ads",
                            "order_qty",
                            "planning_cover_days",
                            "ml_forecast_demand",
                        ]
                        if c in overall.columns
                    ]
                ].rename(
                    columns={
                        "vendor_name": "Vendor",
                        "description": "Product",
                        "current_stock": "On hand",
                        "sold_in_lookback": f"Sold last {ads_window}d",
                        "ads": f"ADS ({ads_window}d)",
                        "order_qty": "Order qty",
                        "planning_cover_days": "Cover (days)",
                        "ml_forecast_demand": "ML forecast",
                    }
                )
                st.caption(f"Sold / ADS use sidebar **Sales lookback = {ads_window} days**.")
                st.dataframe(overall_show, use_container_width=True, hide_index=True)
        else:
            st.info("Click **Load all-vendor summary** for a combined list (slower). Use the vendor picker above for day-to-day ordering.")

    with tab_sched:
        st.markdown("#### Delivery schedules — all vendors")
        if st.button("Load all schedules", key="load_all_schedules"):
            sched_rows = []
            schedule = load_delivery_schedule()
            for _, vr in vendor_df.iterrows():
                _, s = resolve_planning_cover_days(vr["name"], schedule)
                sched_rows.append(
                    {
                        "Vendor": vr["name"],
                        "Mode": "Schedule" if vr["scheduled"] else "Forecast",
                        "Cut-off": s.get("order_cutoff", ""),
                        "Delivery": s.get("delivery_days", ""),
                        "Lead (days)": s.get("lead_time_days", ""),
                        "Frequency": s.get("order_frequency", ""),
                    }
                )
            st.dataframe(pd.DataFrame(sched_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("Click **Load all schedules** only when you need the full table (keeps vendor load fast).")

    with tab_trends:
        if st.button("Load sales trends", key=f"load_trends_{vendor_key}"):
            from app.dashboard.pos_data_service import build_enriched_sales_cached

            enriched = build_enriched_sales_cached()
            if enriched.empty:
                st.info("No sales data.")
            else:
                v_enriched = (
                    enriched[enriched["vendor_name"] == vendor_name]
                    if "vendor_name" in enriched.columns
                    else enriched
                )
                t1, t2 = st.columns(2)
                dt = trend_by_day_type(v_enriched if not v_enriched.empty else enriched)
                if not dt.empty:
                    with t1:
                        st.plotly_chart(
                            _fig_bar(dt, "day_type", "total_units", f"Sales by day type — {vendor_name}"),
                            use_container_width=True,
                        )
                fest = trend_by_festival(enriched)
                if not fest.empty:
                    with t2:
                        st.plotly_chart(
                            _fig_bar(fest, "segment", "avg_daily_units", "Store: festival vs regular"),
                            use_container_width=True,
                        )
        else:
            st.caption("Click **Load sales trends** when you want charts (skipped on normal vendor load).")

    with tab_future:
        if st.button("Load uplift calendar", key=f"load_future_{vendor_key}"):
            from app.dashboard.pos_data_service import build_enriched_sales_cached
            from v2.forecasting.calendar_uplift import forecast_demand_context

            enriched = build_enriched_sales_cached()
            if not use_uplift or enriched.empty:
                st.info("Enable festival / holiday / weekend uplift in the sidebar.")
            else:
                store_daily = enriched.groupby("date", as_index=False)["quantity"].sum()
                ctx = forecast_demand_context(store_daily, horizon_days=30, weather_days=7)
                if not ctx.empty:
                    show_ctx = ctx.head(14).copy()
                    if "date" in show_ctx.columns:
                        show_ctx["date"] = pd.to_datetime(show_ctx["date"]).dt.strftime("%a %b %d")
                    st.dataframe(
                        show_ctx[
                            [
                                c
                                for c in [
                                    "date",
                                    "day_type",
                                    "us_holiday",
                                    "indian_festival",
                                    "weather_label",
                                    "temp_max_f",
                                    "combined_uplift_factor",
                                ]
                                if c in show_ctx.columns
                            ]
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                    fig = _fig_bar(show_ctx, "date", "combined_uplift_factor", "Next 14 days — demand uplift", "day_type")
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.caption("Click **Load uplift calendar** when needed (skipped on normal vendor load).")

    with tab_explain:
        st.markdown("### AI explainability (OpenAI)")
        st.caption("Ask why an item was recommended, the math, or Okemos news impact.")
        with st.expander("Add Okemos / 48864 regional news signal", expanded=False):
            from v2.signals.regional_news import REGION_LABEL, ingest_manual_signal, load_cached_signals

            st.caption(f"Scope: **{REGION_LABEL}** only — not USA-wide.")
            nh = st.text_input("Headline", placeholder="Okemos: fly disease affecting lentils locally")
            nk = st.text_input("Product keywords (comma-separated)", value="lentil,dal,toor,moong,masoor")
            nf = st.slider("Demand factor", 0.70, 1.25, 0.85, 0.01)
            if st.button("Save local news signal", key="save_news_sig"):
                if nh.strip():
                    ingest_manual_signal(
                        headline=nh.strip() + " (Okemos 48864)",
                        product_keywords=[k.strip() for k in nk.split(",") if k.strip()],
                        demand_factor=float(nf),
                        summary=nh.strip(),
                    )
                    clear_all_dashboard_caches()
                    st.success("Signal saved — refresh order list to apply.")
                else:
                    st.warning("Enter a headline.")
            cached = load_cached_signals()
            st.json(cached)

        options = []
        if not products.empty and "description" in products.columns:
            options = products["description"].astype(str).tolist()
        pick = st.selectbox("Product to explain", options[:500] if options else ["(no products)"])
        q = st.text_input(
            "Your question",
            value="Why is this item recommended? Show the mathematical calculation.",
        )
        if st.button("Ask AI", type="primary", key="ask_explain"):
            from api.services.explain_service import build_explain_context, explain_reorder

            row = {}
            if not products.empty and pick and pick != "(no products)":
                hit = products[products["description"].astype(str) == pick]
                if not hit.empty:
                    row = hit.iloc[0].to_dict()
            ctx = build_explain_context(row, vendor=vendor_name)
            answer = explain_reorder(q, ctx)
            st.markdown(answer)

    with st.expander("How this vendor order is calculated"):
        st.markdown(
            """
            **Window 1 — Vendor Intelligent Reorder (Hybrid ML)**
            - Forecast horizon = Vendor Lead Time + Days to Cover
            - Need = max(formula AI Min need, ML horizon forecast − stock)
            - Formula path is the floor inside Hybrid (not a separate mode)
            - Syntetos-Boylan softens ML for intermittent/lumpy items
            - Okemos / 48864 news signals apply soft category factors only
            - Order qty rounded to case/box (50% fill rule)
            """
        )
