"""Global product search — type a name, see which vendors supply it."""

from __future__ import annotations

import streamlit as st

from app.dashboard.product_search_service import (
    build_product_vendor_index,
    search_products,
    vendors_for_query,
)
from app.dashboard.theme import apply_metric_box_theme


@st.cache_data(ttl=600, show_spinner="Building product search index…")
def _cached_product_index():
    return build_product_vendor_index()


def render_global_search_page() -> None:
    apply_metric_box_theme()
    st.title("Global Product Search")
    st.caption(
        "Type any product name (e.g. **lays**, **haldiram**, **basmati**) "
        "to see which **vendors supply it** — from inventory and vendor catalogs."
    )

    query = st.text_input(
        "Search product",
        placeholder="e.g. lays, maggi, ghee, coke…",
        help="Minimum 2 characters",
    )

    if not query or len(query.strip()) < 2:
        st.info("Enter at least 2 characters to search across inventory and all vendor catalogs.")
        return

    try:
        index = _cached_product_index()
    except Exception as exc:
        st.error(f"Could not build search index: {exc}")
        return

    if index.empty:
        st.warning("Search index is empty — check inventory and data/vendors/ catalogs.")
        return

    vendors = vendors_for_query(query, index)
    hits = search_products(query, index, limit=150)

    st.markdown(f"### Vendors supplying **“{query.strip()}”**")
    if vendors.empty:
        st.warning("No vendors found for that search. Try a shorter name or different spelling.")
        return

    v1, v2 = st.columns(2)
    v1.metric("Vendors found", len(vendors))
    v2.metric("Matching products", hits["product_name"].nunique() if not hits.empty else 0)

    st.dataframe(
        vendors.rename(
            columns={
                "vendor_name": "Vendor",
                "vendor_key": "Vendor key",
                "matching_products": "# products matched",
                "sample_products": "Sample products",
                "sources": "Found in",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### All matching products")
    if hits.empty:
        st.caption("No product rows.")
        return

    display = hits[
        ["product_name", "vendor_name", "source", "upc", "unit_cost", "on_hand"]
    ].rename(
        columns={
            "product_name": "Product",
            "vendor_name": "Vendor",
            "source": "Source",
            "upc": "UPC",
            "unit_cost": "Cost ($)",
            "on_hand": "On hand",
        }
    )
    st.dataframe(display, use_container_width=True, hide_index=True)

    st.caption(
        "Sources: **Inventory (POS)** = your current stock file · "
        "**Vendor catalog** = product listed in that vendor’s Excel catalog."
    )
