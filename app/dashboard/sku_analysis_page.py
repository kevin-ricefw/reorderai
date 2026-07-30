"""SKU Analytics dashboard — rankings, LightGBM forecasts, reorder recommendations."""

from __future__ import annotations

import io

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from app.dashboard.pos_data_service import load_inventory, load_sales_detailed
from app.dashboard.sku_analysis_service import (
    analytics_output_dir,
    load_analytics_bundle,
    outputs_exist,
    run_analysis_subprocess,
)
from app.dashboard.theme import apply_metric_box_theme
from v2.analytics.dashboard_constants import DASHBOARD_NOTE


@st.cache_data(ttl=120, show_spinner="Loading SKU analytics…")
def _cached_bundle() -> dict:
    return load_analytics_bundle()


@st.cache_data(ttl=300, show_spinner="Building retail feature correlation EDA…")
def _cached_correlation_eda() -> dict:
    import importlib

    import v2.analytics.feature_correlation_eda as feature_correlation_eda
    import v2.analytics.retail_features as retail_features

    importlib.reload(retail_features)
    importlib.reload(feature_correlation_eda)

    from v2.analytics.sku_sales_metrics import ANALYSIS_END, ANALYSIS_START

    sales = load_sales_detailed(start_date=ANALYSIS_START, end_date=ANALYSIS_END)
    inventory = load_inventory()
    return feature_correlation_eda.build_correlation_eda_bundle(sales, inventory)


def _render_feature_eda_tab() -> None:
    st.subheader("Pre-training feature correlation")
    st.markdown(
        "Heatmap shows **strong retail features only** (lags, rolling averages, weekend, SKU count). "
        "Weak weather, weekday, holidays, price, and school-break signals are removed."
    )
    try:
        eda = _cached_correlation_eda()
    except Exception as exc:
        st.error(f"Could not build correlation EDA: {exc}")
        st.info("Stop the dashboard (Ctrl+C in the terminal), run `run_dashboard.bat` again, then click **Refresh cached results**.")
        return

    daily = eda.get("daily", pd.DataFrame())
    if daily.empty:
        st.warning("No enriched sales data — check data/sales/ CSV exports.")
        return

    d0 = daily["date"].min()
    d1 = daily["date"].max()
    c1, c2, c3 = st.columns(3)
    c1.metric("Days analyzed", len(daily))
    c2.metric("From", str(d0.date()) if hasattr(d0, "date") else str(d0))
    c3.metric("To", str(d1.date()) if hasattr(d1, "date") else str(d1))

    st.plotly_chart(eda["heatmap_figure"], use_container_width=True)

    st.markdown("### What each attribute means")
    guide = eda.get("attribute_guide", pd.DataFrame())
    if not guide.empty:
        st.dataframe(guide, use_container_width=True, hide_index=True)

    st.markdown("### Sample data — first 6 days (real store numbers)")
    sample = eda.get("sample_rows", pd.DataFrame())
    if not sample.empty:
        st.dataframe(sample, use_container_width=True, hide_index=True)
        st.caption("Share `outputs/analytics/feature_sample_6rows.csv` and `feature_attribute_guide.csv` with your team.")

    st.markdown("**Correlations with daily units sold**")
    units_corr = eda.get("correlations_with_units", pd.Series(dtype=float))
    if units_corr.empty:
        st.info("Not enough variation to compute correlations.")
    else:
        show = units_corr.reset_index()
        show.columns = ["Feature", "Correlation"]
        st.dataframe(show, use_container_width=True, hide_index=True)

    with st.expander("Full daily feature table"):
        st.dataframe(daily.head(30), use_container_width=True, hide_index=True)

    st.caption(
        "Full correlation matrices are written under `outputs/analytics/` when analysis runs."
    )


def render_sku_analysis_page() -> None:
    apply_metric_box_theme()
    st.title("SKU Sales Analytics")
    st.caption(
        "Full analysis **Jan 7 – Jul 12, 2026** · LightGBM forecasts · "
        + DASHBOARD_NOTE
    )

    out_dir = analytics_output_dir()

    with st.sidebar:
        st.subheader("SKU Analytics")
        if st.button("Re-run full analysis", use_container_width=True, type="primary"):
            with st.spinner("Running analysis (~1–2 min)…"):
                ok, msg = run_analysis_subprocess()
            if ok:
                st.cache_data.clear()
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)
        if st.button("Refresh cached results", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption(f"Data folder: `{out_dir.name}/`")

    tab_eda, tab_overview, tab_top100, tab_model, tab_reorder, tab_search = st.tabs(
        ["Feature EDA", "Overview", "Top 100", "ML model", "Order now", "Search SKU"]
    )

    with tab_eda:
        _render_feature_eda_tab()

    if not outputs_exist():
        with tab_overview:
            st.warning("No analysis outputs found yet.")
            st.code("python scripts/run_sku_analysis.py", language="powershell")
            st.info("Click **Re-run full analysis** in the sidebar, or run the command above.")
        for tab in (tab_top100, tab_model, tab_reorder, tab_search):
            with tab:
                st.info("Run full SKU analysis to populate this tab.")
        return

    data = _cached_bundle()
    summary = data.get("summary") or {}
    top100 = data.get("top100", pd.DataFrame())
    rankings = data.get("rankings", pd.DataFrame())
    model_eval = data.get("model_eval", pd.DataFrame())
    reorder = data.get("reorder", pd.DataFrame())
    order_now = data.get("order_now", pd.DataFrame())
    master = data.get("master", pd.DataFrame())
    metrics = data.get("metrics", pd.DataFrame())

    period = summary.get("analysis_period", "Jan 7 – Jul 12, 2026")

    # KPIs
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Period", period.split(" to ")[0][-10:] if " to " in period else "2026")
    c2.metric("SKUs sold", f"{summary.get('total_skus_sold', len(metrics)):,}")
    c3.metric("Units sold", f"{summary.get('total_units_sold', 0):,.0f}")
    c4.metric("Revenue", f"${summary.get('total_revenue', 0):,.0f}")
    c5.metric("Order now", f"{summary.get('order_now_count', len(order_now)):,}")
    c6.metric("Model", summary.get("model", "LightGBM"))

    with tab_overview:
        st.subheader("Store performance overview")
        if not metrics.empty:
            o1, o2 = st.columns(2)
            with o1:
                top_rev = metrics.nlargest(15, "total_revenue")
                fig = px.bar(
                    top_rev,
                    x="total_revenue",
                    y="product_name",
                    orientation="h",
                    title="Top 15 by revenue",
                    labels={"total_revenue": "Revenue ($)", "product_name": ""},
                )
                fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=500)
                st.plotly_chart(fig, use_container_width=True)
            with o2:
                top_qty = metrics.nlargest(15, "total_quantity")
                fig = px.bar(
                    top_qty,
                    x="total_quantity",
                    y="product_name",
                    orientation="h",
                    title="Top 15 by quantity",
                    color_discrete_sequence=["#2ecc71"],
                )
                fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=500)
                st.plotly_chart(fig, use_container_width=True)

            fig = make_subplots(rows=1, cols=2, subplot_titles=("Revenue distribution", "ADS distribution"))
            fig.add_trace(
                go.Histogram(x=metrics["total_revenue"], nbinsx=50, name="Revenue"),
                row=1,
                col=1,
            )
            fig.add_trace(
                go.Histogram(x=metrics["ads"], nbinsx=50, name="ADS"),
                row=1,
                col=2,
            )
            fig.update_layout(height=350, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

    with tab_top100:
        st.subheader("Top 100 best sellers")
        st.markdown(
            "**Weighted score:** 50% revenue + 30% quantity + 20% sales frequency "
            "(Jan 7 – Jul 12, 2026)"
        )
        if top100.empty:
            st.info("No top-100 data.")
        else:
            show = top100[
                [
                    "overall_rank",
                    "SKU",
                    "Product Name",
                    "total_revenue",
                    "total_quantity",
                    "transaction_count",
                    "ads",
                    "sales_frequency",
                    "revenue_rank",
                    "quantity_rank",
                    "frequency_rank",
                    "weighted_score",
                    "IsTop100",
                ]
            ].rename(
                columns={
                    "overall_rank": "Rank",
                    "sales_frequency": "Sales freq.",
                    "weighted_score": "Score",
                }
            )
            st.dataframe(show, use_container_width=True, hide_index=True)

            fig = px.scatter(
                top100,
                x="total_revenue",
                y="total_quantity",
                size="weighted_score",
                hover_name="Product Name",
                color="overall_rank",
                title="Top 100 — revenue vs quantity (size = weighted score)",
                labels={"total_revenue": "Revenue ($)", "total_quantity": "Qty sold"},
            )
            st.plotly_chart(fig, use_container_width=True)

            buf = io.BytesIO()
            show.to_csv(buf, index=False)
            st.download_button("Download Top 100 CSV", buf.getvalue(), "top_100_products.csv")

    with tab_model:
        model_comparison = data.get("model_comparison", pd.DataFrame())
        winners = data.get("model_comparison_winners", pd.DataFrame())

        st.subheader("Model benchmark — LightGBM vs Random Forest vs XGBoost")
        st.markdown(
            "Same SKU-day panel and **30-day time holdout** for all three models. "
            "Winner per horizon = highest **R²** on the test set."
        )
        if model_comparison.empty:
            st.info(
                "No benchmark yet. Re-run SKU analysis to refresh model comparison. "
                "or full analysis via **Run analysis**."
            )
        else:
            display_cmp = model_comparison.rename(
                columns={
                    "horizon_days": "Horizon (days)",
                    "model": "Model",
                    "rmse": "RMSE",
                    "mae": "MAE",
                    "mape": "MAPE (%)",
                    "r2": "R²",
                    "train_seconds": "Train (s)",
                    "is_best_r2": "Best R²",
                }
            )
            st.dataframe(display_cmp, use_container_width=True, hide_index=True)

            if not winners.empty:
                st.markdown("**Winners by horizon**")
                st.dataframe(
                    winners.rename(
                        columns={
                            "horizon_days": "Horizon (days)",
                            "model": "Winner",
                            "r2": "R²",
                            "rmse": "RMSE",
                            "mae": "MAE",
                            "train_seconds": "Train (s)",
                        }
                    )[["Horizon (days)", "Winner", "R²", "RMSE", "MAE", "Train (s)"]],
                    use_container_width=True,
                    hide_index=True,
                )

            fig = px.bar(
                model_comparison,
                x="horizon_days",
                y="r2",
                color="model",
                barmode="group",
                title="R² by model and forecast horizon",
                labels={"horizon_days": "Horizon (days)", "r2": "R²", "model": "Model"},
            )
            st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.subheader("Production model — LightGBM")
        st.markdown(
            "Trained on SKU-day panel with **lags**, **rolling sales**, **promotion**, **price**, "
            "**inventory / OOS**, **ROP / safety stock**, and **weekend / school-break** calendar."
        )
        if model_eval.empty:
            st.info("No model evaluation data.")
        else:
            st.dataframe(
                model_eval.rename(
                    columns={
                        "horizon_days": "Horizon (days)",
                        "rmse": "RMSE",
                        "mae": "MAE",
                        "mape": "MAPE (%)",
                        "r2": "R²",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            c1, c2 = st.columns(2)
            with c1:
                fig = px.bar(
                    model_eval,
                    x="horizon_days",
                    y="r2",
                    title="R² by forecast horizon",
                    text=model_eval["r2"].round(3),
                )
                fig.update_traces(textposition="outside")
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                fig = px.bar(
                    model_eval,
                    x="horizon_days",
                    y="mape",
                    title="MAPE (%) by horizon",
                    color_discrete_sequence=["#e74c3c"],
                )
                st.plotly_chart(fig, use_container_width=True)

            fi = data.get("fi_14d", pd.DataFrame())
            if not fi.empty:
                fig = px.bar(
                    fi.head(12),
                    x="importance",
                    y="feature",
                    orientation="h",
                    title="Top features (14-day horizon)",
                )
                fig.update_layout(yaxis={"categoryorder": "total ascending"})
                st.plotly_chart(fig, use_container_width=True)

    with tab_reorder:
        st.subheader("Order now — formula + ML combined")
        st.markdown(
            """
            **AI min** = (ADS × lead time) + safety stock  
            **Order now** = stock ≤ ROP or 7-day forecast exceeds stock  
            **Order qty** = max(formula need, 14-day forecast need), rounded to pack size
            """
        )
        if order_now.empty:
            st.success("No urgent reorders flagged.")
        else:
            st.metric("SKUs to order", len(order_now))
            display = order_now[
                [
                    "upc",
                    "product_name",
                    "vendor_name",
                    "current_inventory",
                    "reorder_point",
                    "ads",
                    "safety_stock",
                    "forecast_7d",
                    "forecast_14d",
                    "forecast_30d",
                    "recommended_order_qty",
                    "days_until_stockout",
                    "confidence_score",
                    "order_now",
                ]
            ].rename(
                columns={
                    "product_name": "Product",
                    "vendor_name": "Vendor",
                    "current_inventory": "On hand",
                    "reorder_point": "ROP",
                    "recommended_order_qty": "Order qty",
                    "days_until_stockout": "Days to stockout",
                    "confidence_score": "Confidence",
                    "order_now": "Order?",
                }
            )
            vendor_filter = st.multiselect(
                "Filter vendor",
                sorted(display["Vendor"].dropna().unique()),
                default=sorted(display["Vendor"].dropna().unique()),
            )
            if vendor_filter:
                display = display[display["Vendor"].isin(vendor_filter)]
            st.dataframe(display, use_container_width=True, hide_index=True)

            buf = io.BytesIO()
            display.to_csv(buf, index=False)
            st.download_button("Download order-now list", buf.getvalue(), "order_now_list.csv")

    with tab_search:
        st.subheader("Look up any SKU")
        if master.empty:
            st.info("No master analysis file.")
            return

        sku_query = st.text_input("Search by UPC or product name")
        df = master.copy()
        if sku_query:
            q = sku_query.strip().lower()
            mask = df["upc"].astype(str).str.lower().str.contains(q, na=False)
            if "Product Name" in df.columns:
                mask = mask | df["Product Name"].astype(str).str.lower().str.contains(q, na=False)
            df = df[mask]

        cols = [
            c
            for c in [
                "upc",
                "Product Name",
                "overall_rank",
                "IsTop100",
                "total_revenue",
                "total_quantity",
                "ads",
                "current_inventory",
                "reorder_point",
                "forecast_7d",
                "forecast_14d",
                "forecast_30d",
                "recommended_order_qty",
                "order_now",
                "days_until_stockout",
                "confidence_score",
                "formula_breakdown",
            ]
            if c in df.columns
        ]
        st.dataframe(df[cols].head(200), use_container_width=True, hide_index=True)
        if len(df) > 200:
            st.caption(f"Showing 200 of {len(df)} matches")

    with st.expander("Output files & re-run command"):
        st.markdown(
            f"""
            All files saved under **`outputs/analytics/`**:

            | File | Description |
            |------|-------------|
            | `sku_master_analysis.csv` | Rankings + forecasts + reorder combined |
            | `top_100_products.csv` | Top 100 best sellers |
            | `sku_reorder_recommendations.csv` | Full reorder math per SKU |
            | `order_now_list.csv` | Urgent orders only |
            | `model_comparison.csv` | LightGBM vs Random Forest vs XGBoost benchmark |
            | `model_comparison_winners.csv` | Best model per horizon (by R²) |
            | `model_evaluation.csv` | Production LightGBM metrics |
            | `sku_demand_forecasts.csv` | 7/14/30-day forecasts |
            | `feature_correlation_matrix.csv` | Pre-training calendar/weather correlation |
            | `feature_correlation_heatmap.html` | Interactive correlation heatmap |

            Re-run: `python scripts/run_sku_analysis.py`  
            Feature EDA outputs: `outputs/analytics/`
            """
        )
        if summary:
            st.json(summary)
