"""Streamlit page — upload sales / inventory and trigger Train Now."""

from __future__ import annotations

from datetime import date, datetime

import streamlit as st

from api.services import train_service, upload_service
from app.dashboard.cache_utils import clear_all_dashboard_caches
from app.dashboard.theme import apply_metric_box_theme


def render_data_upload_page() -> None:
    apply_metric_box_theme()
    st.title("Upload & Train")
    st.caption(
        "Drop new POS sales days and a fresh inventory count, then click **Train now** "
        "to rebuild forecasts and order lists for the store."
    )

    status = upload_service.detect_sales_date_range()
    sales_files = upload_service.list_sales_files()
    inv = upload_service.inventory_status()

    c1, c2, c3 = st.columns(3)
    c1.metric("Sales days on file", len(sales_files))
    if status:
        c2.metric("Sales window", f"{status[0]} → {status[1]}")
    else:
        c2.metric("Sales window", "None yet")
    c3.metric("Inventory UPCs", inv.get("unique_upcs", "—") if inv.get("exists") else "Missing")

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("Upload new sale")
        st.markdown(
            "Use filename `Product Sales JULY 24.csv`, or set the sale date below."
        )
        sales_file = st.file_uploader(
            "Daily Product Sales CSV",
            type=["csv"],
            key="upload_sales_file",
        )
        override_date = st.checkbox("Set sale date manually", value=False)
        sale_date: date | None = None
        if override_date:
            sale_date = st.date_input("Sale date", value=date.today(), key="upload_sale_date")
        if st.button("Upload sales", type="primary", use_container_width=True):
            if sales_file is None:
                st.error("Choose a sales CSV first.")
            else:
                try:
                    result = upload_service.save_sales_upload(
                        sales_file.getvalue(),
                        original_filename=sales_file.name,
                        sale_date=sale_date,
                    )
                    clear_all_dashboard_caches()
                    st.success(
                        f"Saved **{result['saved_as']}** · {result['rows']} rows · {result['sale_date']}"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    with right:
        st.subheader("Upload new inventory count")
        st.markdown("Replaces `current inventory count.csv` used for on-hand qty.")
        inv_file = st.file_uploader(
            "Current inventory count CSV",
            type=["csv"],
            key="upload_inv_file",
        )
        if st.button("Upload inventory", type="primary", use_container_width=True):
            if inv_file is None:
                st.error("Choose an inventory CSV first.")
            else:
                try:
                    result = upload_service.save_inventory_upload(
                        inv_file.getvalue(),
                        original_filename=inv_file.name,
                    )
                    clear_all_dashboard_caches()
                    st.success(
                        f"Inventory updated · **{result['unique_upcs']}** UPCs · {result['rows']} rows"
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    st.markdown("---")
    st.subheader("Train now")
    st.markdown(
        "Retrains LightGBM demand models on all uploaded sales + the latest inventory, "
        "then refreshes reorder outputs."
    )

    latest = train_service.latest_job()
    if latest:
        st.info(
            f"Last job `{latest.get('job_id')}` · **{latest.get('status')}** · "
            f"{latest.get('message', '')}"
        )
        if latest.get("status") == "failed" and latest.get("error"):
            with st.expander("Error details"):
                st.code(latest["error"])
        if latest.get("status") == "completed" and latest.get("summary"):
            s = latest["summary"]
            r1, r2, r3 = st.columns(3)
            r1.metric("Order now", s.get("order_now_count", "—"))
            r2.metric("Model", s.get("model", "—"))
            r3.metric("Period", s.get("analysis_period", "—"))

    if train_service.is_training():
        st.warning("Training is running in the background. Refresh this page in a minute.")
        if st.button("Refresh status", use_container_width=True):
            st.rerun()
        return

    if st.button("Train now", type="primary", use_container_width=True):
        try:
            job = train_service.start_training_job()
            st.success(
                f"Training started (`{job['job_id']}`). "
                f"Window: {job['analysis_start']} → {job['analysis_end']}. "
                "This usually takes a few minutes — refresh for status."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    with st.expander("Recent sales files"):
        if not sales_files:
            st.write("No sales files yet.")
        else:
            st.dataframe(sales_files[-20:], use_container_width=True, hide_index=True)

    st.caption(
        f"API mode also available at `/` when you run the FastAPI server · "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
