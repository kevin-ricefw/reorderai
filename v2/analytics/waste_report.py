"""Parse waste / dump Excel uploads and build summary reports."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

NAME_COLS = ("product", "item", "description", "name", "product name", "product_name")
QTY_COLS = ("quantity", "qty", "units", "count", "dumped", "dump qty", "waste qty", "amount dumped")
UNIT_COST_COLS = ("unit cost", "unit_cost", "cost each", "cost per unit")
TOTAL_COST_COLS = ("total cost", "total_cost", "cost", "amount", "value", "loss", "waste cost", "extended cost")
REASON_COLS = ("reason", "notes", "note", "category", "type")


def _norm_col(c: str) -> str:
    return re.sub(r"\s+", " ", str(c).strip().lower())


def _pick_column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    normed = {_norm_col(c): c for c in columns}
    for cand in candidates:
        if cand in normed:
            return normed[cand]
    for col in columns:
        nc = _norm_col(col)
        for cand in candidates:
            if cand in nc or nc in cand:
                return col
    return None


def _parse_money(val) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    s = str(val).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_waste_upload(
    file_bytes: bytes,
    *,
    filename: str = "upload.xlsx",
    inventory: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Load waste/dump Excel and normalize columns.

    Returns (detail dataframe, column_map used).
    """
    if filename.lower().endswith(".csv"):
        raw = pd.read_csv(io.BytesIO(file_bytes), dtype=str)
    else:
        raw = pd.read_excel(io.BytesIO(file_bytes), dtype=str)

    raw.columns = [str(c).strip() for c in raw.columns]
    if raw.empty:
        return pd.DataFrame(), {}

    name_col = _pick_column(list(raw.columns), NAME_COLS)
    qty_col = _pick_column(list(raw.columns), QTY_COLS)
    unit_col = _pick_column(list(raw.columns), UNIT_COST_COLS)
    total_col = _pick_column(list(raw.columns), TOTAL_COST_COLS)
    reason_col = _pick_column(list(raw.columns), REASON_COLS)

    if not name_col:
        raise ValueError(
            "Could not find a product column. Name it Product, Item, or Description."
        )
    if not qty_col and not total_col:
        raise ValueError(
            "Could not find quantity or cost columns. Add Quantity/Qty and Cost/Total Cost."
        )

    out = pd.DataFrame()
    out["product_name"] = raw[name_col].astype(str).str.strip()
    out = out[out["product_name"].notna() & (out["product_name"] != "") & (out["product_name"].str.upper() != "NAN")]

    out["quantity_dumped"] = (
        pd.to_numeric(raw.loc[out.index, qty_col], errors="coerce").fillna(0) if qty_col else 0.0
    )
    out["unit_cost"] = (
        raw.loc[out.index, unit_col].apply(_parse_money) if unit_col else 0.0
    )
    if total_col:
        out["total_cost"] = raw.loc[out.index, total_col].apply(_parse_money)
    else:
        out["total_cost"] = out["quantity_dumped"] * out["unit_cost"]

    # Fill missing cost from inventory lookup
    if inventory is not None and not inventory.empty:
        inv = inventory.copy()
        inv["description"] = inv["description"].astype(str).str.strip().str.upper()
        inv["cost"] = pd.to_numeric(inv.get("cost"), errors="coerce").fillna(0)
        cost_map = inv.drop_duplicates("description").set_index("description")["cost"].to_dict()
        missing = out["total_cost"] <= 0
        if missing.any():
            upper_names = out.loc[missing, "product_name"].str.upper()
            est_unit = upper_names.map(cost_map).fillna(0)
            out.loc[missing, "unit_cost"] = out.loc[missing, "unit_cost"].where(
                out.loc[missing, "unit_cost"] > 0, est_unit
            )
            out.loc[missing, "total_cost"] = (
                out.loc[missing, "quantity_dumped"] * out.loc[missing, "unit_cost"]
            )

    out["reason"] = raw.loc[out.index, reason_col].astype(str) if reason_col else ""
    out["quantity_dumped"] = out["quantity_dumped"].clip(lower=0)
    out["total_cost"] = out["total_cost"].clip(lower=0)

    col_map = {
        k: v
        for k, v in {
            "product": name_col,
            "quantity": qty_col,
            "unit_cost": unit_col,
            "total_cost": total_col,
            "reason": reason_col,
        }.items()
        if v
    }
    return out.reset_index(drop=True), col_map


def summarize_waste(detail: pd.DataFrame) -> dict:
    """Overall KPIs from parsed waste rows."""
    if detail.empty:
        return {
            "line_items": 0,
            "unique_products": 0,
            "total_units_dumped": 0.0,
            "total_cost": 0.0,
            "avg_cost_per_item": 0.0,
        }
    return {
        "line_items": int(len(detail)),
        "unique_products": int(detail["product_name"].nunique()),
        "total_units_dumped": float(detail["quantity_dumped"].sum()),
        "total_cost": round(float(detail["total_cost"].sum()), 2),
        "avg_cost_per_item": round(float(detail["total_cost"].mean()), 2),
    }


def waste_by_product(detail: pd.DataFrame) -> pd.DataFrame:
    """One row per product with totals."""
    if detail.empty:
        return pd.DataFrame(
            columns=["product_name", "quantity_dumped", "total_cost", "avg_unit_cost", "dump_events"]
        )
    g = (
        detail.groupby("product_name", as_index=False)
        .agg(
            quantity_dumped=("quantity_dumped", "sum"),
            total_cost=("total_cost", "sum"),
            dump_events=("product_name", "count"),
        )
        .sort_values("total_cost", ascending=False)
    )
    g["avg_unit_cost"] = (g["total_cost"] / g["quantity_dumped"].replace(0, pd.NA)).fillna(0).round(2)
    return g


def waste_by_reason(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty or "reason" not in detail.columns:
        return pd.DataFrame()
    sub = detail[detail["reason"].astype(str).str.strip().str.len() > 0]
    if sub.empty:
        return pd.DataFrame()
    return (
        sub.groupby("reason", as_index=False)
        .agg(quantity_dumped=("quantity_dumped", "sum"), total_cost=("total_cost", "sum"), items=("product_name", "count"))
        .sort_values("total_cost", ascending=False)
    )


def save_waste_report(detail: pd.DataFrame, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest_waste_report.csv"
    detail.to_csv(path, index=False)
    summary_path = out_dir / "waste_by_product.csv"
    waste_by_product(detail).to_csv(summary_path, index=False)
    return path
