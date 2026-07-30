"""Validate and persist POS sales / inventory uploads."""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config.data_paths import INVENTORY_PATH, SALES_DIR

MONTH_NAMES = {
    1: "JANUARY",
    2: "FEBRUARY",
    3: "MARCH",
    4: "APRIL",
    5: "MAY",
    6: "JUNE",
    7: "JULY",
    8: "AUGUST",
    9: "SEPTEMBER",
    10: "OCTOBER",
    11: "NOVEMBER",
    12: "DECEMBER",
}

MONTH_MAP = {
    "JAN": 1,
    "JANUARY": 1,
    "FEB": 2,
    "FEBRUARY": 2,
    "MAR": 3,
    "MARCH": 3,
    "APR": 4,
    "APRIL": 4,
    "MAY": 5,
    "JUNE": 6,
    "JULY": 7,
    "AUG": 8,
    "AUGUST": 8,
    "SEP": 9,
    "SEPT": 9,
    "SEPTEMBER": 9,
    "OCT": 10,
    "OCTOBER": 10,
    "NOV": 11,
    "NOVEMBER": 11,
    "DEC": 12,
    "DECEMBER": 12,
}


def _clear_data_caches() -> None:
    try:
        from app.dashboard.cache_utils import clear_all_dashboard_caches

        clear_all_dashboard_caches()
    except Exception:
        pass


def parse_sale_date_from_filename(filename: str, *, default_year: int | None = None) -> date | None:
    """Parse 'Product Sales JULY 23.csv' (optional year) into a date."""
    year = default_year or date.today().year
    name = Path(filename).name
    m = re.match(
        r"Product Sales (\w+)\s+(\d+)(?:\s+(\d{4}))?\.csv$",
        name,
        re.IGNORECASE,
    )
    if not m:
        return None
    month = MONTH_MAP.get(m.group(1).upper())
    if not month:
        return None
    day = int(m.group(2))
    if m.group(3):
        year = int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def sales_filename_for_date(sale_date: date) -> str:
    return f"Product Sales {MONTH_NAMES[sale_date.month]} {sale_date.day}.csv"


def _read_csv_bytes(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content), dtype=str, low_memory=False)


def validate_sales_csv(content: bytes) -> dict[str, Any]:
    """Ensure POS sales export has UPC + Qty Sold."""
    df = _read_csv_bytes(content)
    df.columns = [str(c).strip().strip('"') for c in df.columns]
    upc_col = next((c for c in df.columns if c.upper() == "UPC"), None)
    qty_col = next((c for c in df.columns if "QTY" in c.upper() and "SOLD" in c.upper()), None)
    if not upc_col or not qty_col:
        raise ValueError(
            "Sales file must include columns 'UPC' and 'Qty Sold' (POS Product Sales export)."
        )
    rows = int(len(df))
    return {
        "rows": rows,
        "columns": list(df.columns),
        "upc_column": upc_col,
        "qty_column": qty_col,
    }


def validate_inventory_csv(content: bytes) -> dict[str, Any]:
    """Ensure inventory count export has upc + QuantityOnHand."""
    df = _read_csv_bytes(content)
    df.columns = [str(c).strip() for c in df.columns]
    cols_lower = {c.lower(): c for c in df.columns}
    upc_col = cols_lower.get("upc")
    qty_col = cols_lower.get("quantityonhand") or next(
        (c for c in df.columns if "quantity" in c.lower() and "hand" in c.lower()),
        None,
    )
    if not upc_col or not qty_col:
        raise ValueError(
            "Inventory file must include columns 'upc' and 'QuantityOnHand' "
            "(current inventory count export)."
        )
    return {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "upc_column": upc_col,
        "qty_column": qty_col,
        "unique_upcs": int(df[upc_col].astype(str).str.strip().nunique()),
    }


def save_sales_upload(
    content: bytes,
    *,
    original_filename: str,
    sale_date: date | None = None,
    default_year: int | None = None,
) -> dict[str, Any]:
    """
    Validate and write a daily sales CSV into data/sales/.

    Filename becomes Product Sales {MONTH} {DAY}.csv so the loader can parse it.
    """
    meta = validate_sales_csv(content)
    resolved = sale_date or parse_sale_date_from_filename(
        original_filename, default_year=default_year
    )
    if resolved is None:
        raise ValueError(
            "Could not determine sale date. Use filename like "
            "'Product Sales JULY 23.csv' or pass sale_date=YYYY-MM-DD."
        )

    SALES_DIR.mkdir(parents=True, exist_ok=True)
    dest_name = sales_filename_for_date(resolved)
    dest = SALES_DIR / dest_name
    replaced = dest.exists()
    dest.write_bytes(content)
    _clear_data_caches()

    return {
        "ok": True,
        "kind": "sales",
        "sale_date": resolved.isoformat(),
        "saved_as": dest_name,
        "path": str(dest),
        "replaced_existing": replaced,
        **meta,
    }


def save_inventory_upload(content: bytes, *, original_filename: str = "") -> dict[str, Any]:
    """Validate and replace data/inventory/current inventory count.csv."""
    meta = validate_inventory_csv(content)
    INVENTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    INVENTORY_PATH.write_bytes(content)
    _clear_data_caches()
    return {
        "ok": True,
        "kind": "inventory",
        "saved_as": INVENTORY_PATH.name,
        "path": str(INVENTORY_PATH),
        "original_filename": original_filename,
        **meta,
    }


def list_sales_files(*, default_year: int | None = None) -> list[dict[str, Any]]:
    """List uploaded daily sales files with parsed dates."""
    year = default_year or date.today().year
    if not SALES_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(SALES_DIR.glob("Product Sales *.csv")):
        d = parse_sale_date_from_filename(path.name, default_year=year)
        items.append(
            {
                "filename": path.name,
                "sale_date": d.isoformat() if d else None,
                "size_bytes": path.stat().st_size,
                "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            }
        )
    return items


def inventory_status() -> dict[str, Any]:
    if not INVENTORY_PATH.exists():
        return {"exists": False, "path": str(INVENTORY_PATH)}
    try:
        content = INVENTORY_PATH.read_bytes()
        meta = validate_inventory_csv(content)
        st = INVENTORY_PATH.stat()
        return {
            "exists": True,
            "path": str(INVENTORY_PATH),
            "size_bytes": st.st_size,
            "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            **meta,
        }
    except Exception as exc:
        return {
            "exists": True,
            "path": str(INVENTORY_PATH),
            "error": str(exc),
        }


def detect_sales_date_range(*, default_year: int | None = None) -> tuple[date, date] | None:
    """Min/max sale dates from files on disk."""
    year = default_year or date.today().year
    dates: list[date] = []
    if not SALES_DIR.exists():
        return None
    for path in SALES_DIR.glob("Product Sales *.csv"):
        d = parse_sale_date_from_filename(path.name, default_year=year)
        if d:
            dates.append(d)
    if not dates:
        return None
    return min(dates), max(dates)
