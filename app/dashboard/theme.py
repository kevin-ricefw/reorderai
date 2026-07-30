"""Metric box styling and vendor reorder page polish."""

from __future__ import annotations

import streamlit as st

METRIC_BOX_CSS = """
<style>
    [data-testid="stMetric"] {
        background: linear-gradient(145deg, #1a1a1a 0%, #0d1117 100%) !important;
        border: 1px solid #00ff6622 !important;
        border-radius: 12px;
        padding: 0.85rem 1rem;
        box-shadow: 0 4px 14px rgba(0, 255, 102, 0.12);
    }
    [data-testid="stMetricLabel"] {
        color: #7ee787 !important;
        font-weight: 600;
        font-size: 0.8rem !important;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    [data-testid="stMetricValue"] {
        color: #3fb950 !important;
        font-size: 1.55rem !important;
        font-weight: 700;
    }
    [data-testid="stMetricDelta"] {
        color: #56d364 !important;
    }
    .block-container { padding-top: 1.25rem; max-width: 1400px; }
    .vendor-hero {
        background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #1a2332 100%);
        border: 1px solid #30363d;
        border-radius: 16px;
        padding: 1.5rem 1.75rem;
        margin-bottom: 1.25rem;
        box-shadow: 0 8px 24px rgba(0,0,0,0.35);
    }
    .vendor-hero h2 {
        color: #f0f6fc;
        margin: 0 0 0.35rem 0;
        font-size: 1.65rem;
        font-weight: 700;
    }
    .vendor-hero p { color: #8b949e; margin: 0; font-size: 0.95rem; }
    .badge-scheduled {
        display: inline-block;
        background: #1f6feb33;
        color: #58a6ff;
        border: 1px solid #388bfd66;
        border-radius: 999px;
        padding: 0.25rem 0.75rem;
        font-size: 0.78rem;
        font-weight: 600;
        margin-right: 0.5rem;
    }
    .badge-forecast {
        display: inline-block;
        background: #23863633;
        color: #3fb950;
        border: 1px solid #3fb95066;
        border-radius: 999px;
        padding: 0.25rem 0.75rem;
        font-size: 0.78rem;
        font-weight: 600;
        margin-right: 0.5rem;
    }
    .sched-card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 1rem 1.15rem;
        height: 100%;
    }
    .sched-card .label { color: #8b949e; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; }
    .sched-card .value { color: #f0f6fc; font-size: 1.05rem; font-weight: 600; margin-top: 0.25rem; }
    div[data-testid="stTabs"] button[data-baseweb="tab"] {
        font-weight: 600;
    }
</style>
"""


def apply_metric_box_theme() -> None:
    st.markdown(METRIC_BOX_CSS, unsafe_allow_html=True)


def vendor_hero(title: str, subtitle: str, badge_html: str) -> None:
    st.markdown(
        f'<div class="vendor-hero"><h2>{title}</h2><p>{subtitle}</p>'
        f'<div style="margin-top:0.75rem">{badge_html}</div></div>',
        unsafe_allow_html=True,
    )


def sched_info_card(label: str, value: str) -> None:
    st.markdown(
        f'<div class="sched-card"><div class="label">{label}</div><div class="value">{value}</div></div>',
        unsafe_allow_html=True,
    )