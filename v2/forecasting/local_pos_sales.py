"""
Load dated POS daily sales from local ``data/sales/Product Sales*.csv``.

Filenames like ``Product Sales APRIL 1.csv`` carry the calendar day used for
weekday / weekend / festival uplift. Qty Sold is the demand signal.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from config.data_paths import SALES_DIR

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_NAME_RE = re.compile(
    r"Product Sales\s+([A-Za-z]+)\s+(\d{1,2})(?:\s+(\d{4}))?",
    re.IGNORECASE,
)


def normalize_upc(value: object) -> str:
    s = re.sub(r"\D", "", str(value or ""))
    if not s:
        return ""
    # Keep POS-style zero padding when present; also expose bare digits.
    return s.lstrip("0") or "0"


def parse_sale_date_from_filename(path: Path, *, default_year: int = 2026) -> date | None:
    m = _NAME_RE.search(path.stem)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower())
    if not month:
        return None
    day = int(m.group(2))
    year = int(m.group(3)) if m.group(3) else default_year
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _read_one_sales_csv(path: Path, sale_date: date) -> pd.DataFrame:
    try:
        raw = pd.read_csv(path, dtype=str)
    except Exception:
        return pd.DataFrame()
    if raw.empty:
        return pd.DataFrame()

    cols = {c.strip().lower(): c for c in raw.columns}
    upc_col = cols.get("upc")
    qty_col = cols.get("qty sold") or cols.get("qty_sold") or cols.get("quantity")
    if not upc_col or not qty_col:
        return pd.DataFrame()

    desc_col = cols.get("description")
    net_col = cols.get("net sales")
    out = pd.DataFrame(
        {
            "upc_raw": raw[upc_col].astype(str),
            "upc": raw[upc_col].map(normalize_upc),
            "quantity": pd.to_numeric(
                raw[qty_col].astype(str).str.replace(",", "", regex=False),
                errors="coerce",
            ).fillna(0.0),
            "description": raw[desc_col].astype(str) if desc_col else "",
            "net_sales": (
                pd.to_numeric(
                    raw[net_col].astype(str).str.replace(r"[$,]", "", regex=True),
                    errors="coerce",
                ).fillna(0.0)
                if net_col
                else 0.0
            ),
            "sale_date": sale_date,
            "source_file": path.name,
        }
    )
    out = out[out["upc"] != ""]
    out = out[out["quantity"] > 0]
    return out


def load_local_pos_daily_sales(
    *,
    sales_dir: Path | None = None,
    default_year: int = 2026,
    lookback_days: int | None = None,
    as_of: date | None = None,
) -> pd.DataFrame:
    """
    Returns columns: upc, sale_date, quantity, description, net_sales, source_file
    One row per UPC per file day (aggregated if duplicates).
    """
    root = sales_dir or SALES_DIR
    if not root.exists():
        return pd.DataFrame(
            columns=["upc", "sale_date", "quantity", "description", "net_sales", "source_file"]
        )

    frames: list[pd.DataFrame] = []
    for path in sorted(root.rglob("Product Sales*.csv")):
        if path.name.lower().startswith("sales_from_paul"):
            continue
        sale_date = parse_sale_date_from_filename(path, default_year=default_year)
        if sale_date is None:
            continue
        part = _read_one_sales_csv(path, sale_date)
        if not part.empty:
            frames.append(part)

    if not frames:
        return pd.DataFrame(
            columns=["upc", "sale_date", "quantity", "description", "net_sales", "source_file"]
        )

    df = pd.concat(frames, ignore_index=True)
    df = (
        df.groupby(["upc", "sale_date", "source_file"], as_index=False)
        .agg(
            quantity=("quantity", "sum"),
            description=("description", "first"),
            net_sales=("net_sales", "sum"),
        )
    )

    if lookback_days is not None:
        end = as_of or date.today()
        start = end.toordinal() - max(int(lookback_days), 1) + 1
        start_d = date.fromordinal(start)
        df = df[df["sale_date"] >= start_d]

    df["sale_date"] = pd.to_datetime(df["sale_date"])
    return df.sort_values(["sale_date", "upc"]).reset_index(drop=True)


def local_sales_to_demand(
    sales: pd.DataFrame,
    *,
    upc_to_item_id: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Map local POS rows → forecast demand frame: item_id, date, quantity.

    If upc_to_item_id is provided (Paul product_barcodes), item_id = product_id.
    Otherwise item_id = normalized UPC (works offline; map later for detect-order).
    """
    if sales.empty:
        return pd.DataFrame(columns=["item_id", "date", "quantity"])

    mapping = upc_to_item_id or {}
    item_ids: list[str] = []
    for upc in sales["upc"].astype(str):
        item_ids.append(mapping.get(upc) or mapping.get(upc.zfill(13)) or upc)

    out = pd.DataFrame(
        {
            "item_id": item_ids,
            "date": pd.to_datetime(sales["sale_date"]),
            "quantity": pd.to_numeric(sales["quantity"], errors="coerce").fillna(0.0),
        }
    )
    return (
        out.groupby(["item_id", "date"], as_index=False)["quantity"]
        .sum()
        .sort_values(["item_id", "date"])
        .reset_index(drop=True)
    )


def calendar_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Attach weekday / weekend flags for uplift analysis."""
    if daily.empty:
        return daily
    out = daily.copy()
    out["date"] = pd.to_datetime(out["date"])
    out["weekday"] = out["date"].dt.day_name()
    out["is_weekend"] = out["date"].dt.dayofweek >= 5
    out["month"] = out["date"].dt.month
    out["weekofyear"] = out["date"].dt.isocalendar().week.astype(int)
    return out
