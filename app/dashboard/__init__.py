"""Streamlit dashboard — Layer 4 UI."""


def run_dashboard():
    from app.dashboard.main import run_dashboard as _run

    return _run()


__all__ = ["run_dashboard"]
