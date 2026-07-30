"""Waste / dump report — upload Excel and view totals."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from app.dashboard.pos_data_service import load_inventory
from app.dashboard.theme import apply_metric_box_theme
from config.data_paths import WASTE_DIR
from v2.analytics.waste_report import (
    parse_waste_upload,
    save_waste_report,
    summarize_waste,
    waste_by_product,
    waste_by_reason,
)

WASTE_DIR.mkdir(parents=True, exist_ok=True)


def render_waste_report_page() -> None:
    apply_metric_box_theme()
    st.title("Waste / Dump Report")
    st.caption(
        "Upload an Excel or CSV of dumped/waste items. "
        "We summarize units dumped, cost per item, and total loss."
    )

    st.markdown(
        """
        **Expected columns** (flexible names):

        | You can name it | Examples |
        |-----------------|----------|
        | Product | Product, Item, Description |
        | Quantity | Qty, Units, Dumped |
        | Cost | Cost, Total Cost, Amount, Loss |

        Optional: **Reason** or **Notes** column.
        """
    )

    uploaded = st.file_uploader(
        "Upload waste / dump sheet",
        type=["xlsx", "xls", "csv"],
        help="Excel or CSV export from your waste log",
    )

    inventory = load_inventory()

    if uploaded is None:
        _show_saved_report_hint()
        return

    try:
        detail, col_map = parse_waste_upload(
            uploaded.getvalue(),
            filename=uploaded.name,
            inventory=inventory,
        )
    except Exception as exc:
        st.error(str(exc))
        return

    if detail.empty:
        st.warning("No rows found in the uploaded file.")
        return

    if col_map:
        st.success(f"Loaded **{len(detail)}** rows · mapped columns: `{col_map}`")

    summary = summarize_waste(detail)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Line items", summary["line_items"])
    c2.metric("Unique products", summary["unique_products"])
    c3.metric("Units dumped", f"{summary['total_units_dumped']:,.0f}")
    c4.metric("Total waste cost", f"${summary['total_cost']:,.2f}")

    by_product = waste_by_product(detail)
    by_reason = waste_by_reason(detail)

    tab_items, tab_chart, tab_raw = st.tabs(["By product", "Chart", "All rows"])

    with tab_items:
        st.subheader("Items dumped — quantity & cost")
        show = by_product.rename(
            columns={
                "product_name": "Product",
                "quantity_dumped": "Units dumped",
                "total_cost": "Total cost ($)",
                "avg_unit_cost": "Avg unit cost ($)",
                "dump_events": "Entries",
            }
        )
        st.dataframe(show, use_container_width=True, hide_index=True)

        if not by_reason.empty:
            st.subheader("By reason / category")
            st.dataframe(by_reason, use_container_width=True, hide_index=True)

    with tab_chart:
        top = by_product.head(20)
        if not top.empty:
            fig = px.bar(
                top,
                x="total_cost",
                y="product_name",
                orientation="h",
                title="Top 20 products by waste cost ($)",
                labels={"total_cost": "Cost ($)", "product_name": ""},
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, height=560)
            st.plotly_chart(fig, use_container_width=True)

    with tab_raw:
        st.dataframe(detail, use_container_width=True, hide_index=True)

    if st.button("Save report to data/waste/", type="primary"):
        path = save_waste_report(detail, WASTE_DIR)
        st.success(f"Saved: `{path}` and `waste_by_product.csv`")

    buf = io.BytesIO()
    by_product.to_csv(buf, index=False)
    st.download_button(
        "Download summary CSV",
        buf.getvalue(),
        file_name="waste_by_product.csv",
        mime="text/csv",
    )


def _show_saved_report_hint() -> None:
    demo = WASTE_DIR / "DEMO_Waste_Dump_Report.xlsx"
    if demo.exists():
        st.success(
            f"**Sample file:** `{demo.name}`  \n"
            f"Path: `{demo}`  \n"
            "Upload that Excel here to generate the waste / dump report."
        )
    latest = WASTE_DIR / "waste_by_product.csv"
    if latest.exists():
        st.info("Last saved report found — upload a new file or view previous summary below.")
        prev = pd.read_csv(latest)
        st.dataframe(prev, use_container_width=True, hide_index=True)
    else:
        st.info("Upload a waste Excel/CSV file to generate your first report.")
