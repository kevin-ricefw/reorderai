"""
Streamlit Dashboard — POS reorder & SKU analytics.

Run: python -m streamlit run app/dashboard/main.py
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)

from config.settings import get_settings

get_settings.cache_clear()

import streamlit as st

from app.dashboard.data_source import data_source_label
from app.dashboard.cache_utils import clear_all_dashboard_caches
from app.dashboard.data_upload_page import render_data_upload_page
from app.dashboard.global_search_page import render_global_search_page
from app.dashboard.methodology_page import render_methodology_page
from app.dashboard.sku_analysis_page import render_sku_analysis_page
from app.dashboard.theme import apply_metric_box_theme
from app.dashboard.vendor_reorder_page import render_vendor_reorder_page
from app.dashboard.waste_report_page import render_waste_report_page


def run_dashboard() -> None:
    settings = get_settings()
    st.set_page_config(
        page_title="Inventory AI — Reorder & Analytics",
        page_icon="📦",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    apply_metric_box_theme()

    st.sidebar.title("Inventory AI")
    st.sidebar.caption(settings.app.name)
    st.sidebar.info(f"Data source: **{data_source_label()}**")

    view = st.sidebar.radio(
        "Dashboard",
        [
            "Upload & Train",
            "Vendor Reorder Planner",
            "How Everything Works",
            "SKU Sales Analytics",
            "Global Product Search",
            "Waste / Dump Report",
        ],
        index=0,
        help="Real POS data: inventory + daily sales exports",
    )

    if st.sidebar.button("Refresh all data", use_container_width=True):
        clear_all_dashboard_caches()
        st.rerun()

    if view == "Upload & Train":
        render_data_upload_page()
    elif view == "Vendor Reorder Planner":
        render_vendor_reorder_page()
    elif view == "How Everything Works":
        render_methodology_page()
    elif view == "SKU Sales Analytics":
        render_sku_analysis_page()
    elif view == "Global Product Search":
        render_global_search_page()
    else:
        render_waste_report_page()


if __name__ == "__main__":
    run_dashboard()
