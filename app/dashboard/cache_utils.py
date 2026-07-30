"""Clear Streamlit and functools caches after data / logic updates."""

from __future__ import annotations


def clear_all_dashboard_caches() -> None:
    """Drop cached inventory, sales, vendor lists, and Streamlit memoization."""
    try:
        from app.dashboard.pos_data_service import (
            _load_daily_sales_cached,
            build_enriched_sales_cached,
            sales_date_span,
        )

        _load_daily_sales_cached.cache_clear()
        build_enriched_sales_cached.cache_clear()
        sales_date_span.cache_clear()
    except Exception:
        pass

    try:
        from app.dashboard.vendor_catalog_loader import (
            _load_delivery_schedule_cached,
            _load_vendor_catalog_cached,
            get_all_store_vendors,
        )

        get_all_store_vendors.cache_clear()
        _load_delivery_schedule_cached.cache_clear()
        _load_vendor_catalog_cached.cache_clear()
    except Exception:
        pass

    try:
        from database.readers.sandbox_data_reader import get_sandbox_reader, sandbox_db_available

        get_sandbox_reader.cache_clear()
        sandbox_db_available.cache_clear()
    except Exception:
        pass

    try:
        from app.dashboard.past_invoice_patterns import clear_invoice_caches

        clear_invoice_caches()
    except Exception:
        pass

    try:
        from config.settings import get_settings

        get_settings.cache_clear()
    except Exception:
        pass

    try:
        import streamlit as st

        st.cache_data.clear()
    except Exception:
        pass


def inventory_vendor_names_look_broken() -> bool:
    """True when most SKUs have vendor_name Unknown (stale sandbox read)."""
    try:
        from app.dashboard.pos_data_service import load_inventory

        inv = load_inventory()
        if inv.empty or "vendor_name" not in inv.columns:
            return False
        unknown = (inv["vendor_name"].astype(str).str.strip().str.upper() == "UNKNOWN").sum()
        return unknown / len(inv) > 0.25
    except Exception:
        return False
