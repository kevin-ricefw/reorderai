"""Load or run full SKU analytics pipeline for the dashboard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "outputs" / "analytics"
ANALYSIS_SCRIPT = PROJECT_ROOT / "scripts" / "run_sku_analysis.py"


def analytics_output_dir() -> Path:
    return OUTPUT_DIR


def outputs_exist() -> bool:
    required = [
        OUTPUT_DIR / "sku_master_analysis.csv",
        OUTPUT_DIR / "top_100_products.csv",
        OUTPUT_DIR / "model_evaluation.csv",
    ]
    return all(p.exists() for p in required)


def run_analysis_subprocess() -> tuple[bool, str]:
    """Run full analysis script (~1-2 min). Returns (success, message)."""
    try:
        result = subprocess.run(
            [sys.executable, str(ANALYSIS_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            return False, result.stderr or result.stdout or "Analysis failed"
        return True, "Analysis completed successfully."
    except subprocess.TimeoutExpired:
        return False, "Analysis timed out after 10 minutes."
    except Exception as exc:
        return False, str(exc)


def load_summary() -> dict[str, Any]:
    path = OUTPUT_DIR / "analysis_summary.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_upc_series(series: pd.Series) -> pd.Series:
    """Keep UPCs as zero-padded strings so joins match inventory."""
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"^nan$", "", regex=True)
    )


def _vendor_lookup() -> pd.DataFrame:
    """UPC → vendor_name from live inventory (POS CSV / sandbox overlay)."""
    try:
        from app.dashboard.pos_data_service import load_inventory

        inv = load_inventory()
    except Exception:
        return pd.DataFrame(columns=["upc", "vendor_name"])
    if inv.empty or "upc" not in inv.columns:
        return pd.DataFrame(columns=["upc", "vendor_name"])
    out = inv[["upc", "vendor_name"]].copy()
    out["upc"] = _normalize_upc_series(out["upc"])
    out["vendor_name"] = (
        out["vendor_name"].fillna("Unknown").astype(str).str.strip().replace({"": "Unknown"})
    )
    return out.drop_duplicates(subset=["upc"], keep="last")


def _overlay_vendors(df: pd.DataFrame, *, upc_col: str = "upc") -> pd.DataFrame:
    """Replace missing/Unknown vendor_name using live inventory."""
    if df.empty:
        return df
    col = upc_col if upc_col in df.columns else ("SKU" if "SKU" in df.columns else None)
    if col is None or "vendor_name" not in df.columns:
        return df

    vendors = _vendor_lookup()
    if vendors.empty:
        return df

    out = df.copy()
    out[col] = _normalize_upc_series(out[col])
    merged = out[[col]].merge(vendors, left_on=col, right_on="upc", how="left")
    live = merged["vendor_name"]
    current = out["vendor_name"].fillna("Unknown").astype(str).str.strip()
    needs_fix = current.isin(("", "Unknown", "nan", "None")) | current.isna()
    out["vendor_name"] = current.where(~needs_fix, live.fillna("Unknown"))
    out["vendor_name"] = out["vendor_name"].fillna("Unknown").astype(str).str.strip()
    return out


def load_analytics_bundle() -> dict[str, Any]:
    """Load all analytics CSV outputs for the dashboard."""

    def _read(name: str) -> pd.DataFrame:
        path = OUTPUT_DIR / name
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path, dtype=str, low_memory=False)
        for col in ("upc", "SKU"):
            if col in df.columns:
                df[col] = _normalize_upc_series(df[col])
        skip = {
            "upc",
            "SKU",
            "product_name",
            "Product Name",
            "vendor_name",
            "order_now",
            "IsTop100",
            "formula_breakdown",
            "forecast_date",
            "model",
            "feature",
        }
        for col in df.columns:
            if col in skip or df[col].dtype != object:
                continue
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().mean() >= 0.8:
                df[col] = converted
        return df

    order_now = _overlay_vendors(_read("order_now_list.csv"))
    summary = load_summary()
    if not order_now.empty:
        summary = {**summary, "order_now_count": int(len(order_now))}

    return {
        "summary": summary,
        "metrics": _overlay_vendors(_read("sku_sales_metrics.csv")),
        "rankings": _read("sku_rankings_top100.csv"),
        "top100": _read("top_100_products.csv"),
        "model_eval": _read("model_evaluation.csv"),
        "model_comparison": _read("model_comparison.csv"),
        "model_comparison_winners": _read("model_comparison_winners.csv"),
        "forecasts": _read("sku_demand_forecasts.csv"),
        "reorder": _overlay_vendors(_read("sku_reorder_recommendations.csv")),
        "master": _overlay_vendors(_read("sku_master_analysis.csv")),
        "order_now": order_now,
        "fi_7d": _read("feature_importance_7d.csv"),
        "fi_14d": _read("feature_importance_14d.csv"),
        "fi_30d": _read("feature_importance_30d.csv"),
    }
