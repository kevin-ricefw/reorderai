"""Load POS inventory and daily sales CSV exports."""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from config.data_paths import INVENTORY_PATH, PACK_OVERRIDES_PATH, PROJECT_ROOT, SALES_DIR

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


def _parse_sales_filename(path: Path, *, default_year: int = 2026) -> date | None:
    m = re.match(r"Product Sales (\w+)\s+(\d+)\.csv$", path.name, re.IGNORECASE)
    if not m:
        return None
    month = MONTH_MAP.get(m.group(1).upper())
    if not month:
        return None
    return date(default_year, month, int(m.group(2)))


def _apply_pack_overrides(df: pd.DataFrame) -> pd.DataFrame:
    """Apply vendor case/pack sizes that POS inventory often leaves blank."""
    if df.empty or not PACK_OVERRIDES_PATH.exists():
        return df
    try:
        overrides = pd.read_csv(PACK_OVERRIDES_PATH, dtype={"upc": str})
    except Exception:
        return df
    if overrides.empty or "upc" not in overrides.columns or "pack" not in overrides.columns:
        return df

    overrides["upc"] = overrides["upc"].astype(str).str.strip()
    overrides["pack"] = pd.to_numeric(overrides["pack"], errors="coerce")
    overrides = overrides.dropna(subset=["upc", "pack"])
    overrides = overrides[overrides["pack"] > 0].drop_duplicates(subset=["upc"], keep="first")
    if overrides.empty:
        return df

    out = df.copy()
    out["upc"] = out["upc"].astype(str).str.strip()
    pack_map = dict(zip(overrides["upc"], overrides["pack"].astype(int)))
    mapped = out["upc"].map(pack_map)
    # Overrides always win when present (catalog/POS may have pack=1 blanks)
    if "pack" not in out.columns:
        out["pack"] = mapped
    else:
        out["pack"] = mapped.where(mapped.notna(), out["pack"])
    return out


def save_pack_overrides(
    updates: list[dict[str, Any]] | pd.DataFrame,
    *,
    vendor_label: str = "",
) -> int:
    """
    Upsert UPC → units-per-case into data/vendors/pack_overrides.csv.

    User edits from the Vendor Reorder table win over prior rows.
    Returns number of UPCs written/updated.
    """
    if isinstance(updates, pd.DataFrame):
        rows = updates.copy()
    else:
        rows = pd.DataFrame(updates)
    if rows.empty:
        return 0

    # Accept either column names from UI or raw upc/pack
    rename = {
        "UPC": "upc",
        "Units in 1 case": "pack",
        "Product": "notes",
        "pack_size": "pack",
    }
    rows = rows.rename(columns={k: v for k, v in rename.items() if k in rows.columns})
    if "upc" not in rows.columns or "pack" not in rows.columns:
        return 0

    rows["upc"] = rows["upc"].astype(str).str.strip()
    rows["pack"] = pd.to_numeric(rows["pack"], errors="coerce")
    rows = rows.dropna(subset=["upc", "pack"])
    rows = rows[rows["pack"] > 0]
    rows["pack"] = rows["pack"].astype(int)
    if "notes" not in rows.columns:
        rows["notes"] = ""
    rows["notes"] = rows["notes"].astype(str)
    if vendor_label:
        rows["notes"] = rows.apply(
            lambda r: r["notes"]
            if "UI edit" in str(r["notes"])
            else f"{r['notes']} — UI edit ({vendor_label})".strip(" —"),
            axis=1,
        )
    rows = rows[["upc", "pack", "notes"]].drop_duplicates(subset=["upc"], keep="last")
    if rows.empty:
        return 0

    PACK_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if PACK_OVERRIDES_PATH.exists():
        existing = pd.read_csv(PACK_OVERRIDES_PATH, dtype={"upc": str})
        existing["upc"] = existing["upc"].astype(str).str.strip()
        # New edits first so keep="first" in _apply_pack_overrides prefers them
        merged = pd.concat([rows, existing], ignore_index=True)
        merged = merged.drop_duplicates(subset=["upc"], keep="first")
    else:
        merged = rows

    merged.to_csv(PACK_OVERRIDES_PATH, index=False)
    return int(len(rows))


def _load_inventory_from_csv(path: Path | None = None) -> pd.DataFrame:
    """Load current inventory count export from CSV."""
    p = path or INVENTORY_PATH
    df = pd.read_csv(p, dtype=str, low_memory=False)
    df = df.rename(columns={c: c.strip() for c in df.columns})

    for col in ("pack", "QuantityOnHand", "LowInventoryAlert", "case_cost", "cost", "normal_price"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["upc"] = df["upc"].astype(str).str.strip()
    df["vendor_name"] = df.get("vendor_name", pd.Series(dtype=str)).fillna("Unknown").astype(str).str.strip()
    df["description"] = df.get("description", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
    df["active"] = df.get("active", "True").astype(str).str.lower().isin(("true", "1", "yes"))
    return _apply_pack_overrides(df)


# POS CSV fields always win over sandbox DB (vendor grouping + on-hand qty)
_POS_OVERLAY_COLS = (
    "vendor_name",
    "QuantityOnHand",
    "dept_name",
    "section_name",
    "pack",
    "case_cost",
    "active",
    "description",
)


def _overlay_pos_csv_inventory(df: pd.DataFrame) -> pd.DataFrame:
    """Merge live POS inventory CSV — required for vendor reorder when using sandbox DB."""
    if df.empty or not INVENTORY_PATH.exists():
        return df

    csv = _load_inventory_from_csv()
    keep = ["upc"] + [c for c in _POS_OVERLAY_COLS if c in csv.columns]
    csv = csv[keep].drop_duplicates(subset=["upc"], keep="last")
    csv["upc"] = csv["upc"].astype(str).str.strip()

    out = df.copy()
    out["upc"] = out["upc"].astype(str).str.strip()
    out = out.drop(columns=[c for c in _POS_OVERLAY_COLS if c in out.columns], errors="ignore")
    out = out.merge(csv, on="upc", how="left")

    if "vendor_name" in out.columns:
        out["vendor_name"] = out["vendor_name"].fillna("Unknown").astype(str).str.strip()
    if "active" in out.columns:
        out["active"] = out["active"].fillna(True).astype(bool)
    return out


def load_inventory(path: Path | None = None, *, use_db: bool | None = None) -> pd.DataFrame:
    """Load inventory — PostgreSQL sandbox when available, always overlay POS CSV fields."""
    if path is not None:
        return _load_inventory_from_csv(path)

    if use_db is None:
        from database.readers.sandbox_data_reader import sandbox_db_available

        use_db = sandbox_db_available()

    if use_db:
        try:
            from database.readers.sandbox_data_reader import get_sandbox_reader

            df = get_sandbox_reader().load_inventory()
            if not df.empty:
                return _overlay_pos_csv_inventory(df)
        except Exception:
            pass

    return _load_inventory_from_csv()


def _parse_money(value: str | float | None) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    s = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _load_sales_detailed_from_csv(
    sales_dir: Path | None = None,
    *,
    default_year: int = 2026,
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame:
    """
    Load daily sales with quantity, revenue, transaction count, promotion flag.

    Returns one row per UPC × date with aggregated metrics.
    """
    directory = sales_dir or SALES_DIR
    rows: list[dict] = []

    for path in sorted(directory.glob("Product Sales *.csv")):
        sale_date = _parse_sales_filename(path, default_year=default_year)
        if not sale_date:
            continue
        if start_date and sale_date < start_date:
            continue
        if end_date and sale_date > end_date:
            continue
        try:
            day = pd.read_csv(path, dtype=str)
        except Exception:
            continue
        day.columns = [c.strip().strip('"') for c in day.columns]
        upc_col = next((c for c in day.columns if c.upper() == "UPC"), None)
        qty_col = next((c for c in day.columns if "QTY" in c.upper() and "SOLD" in c.upper()), None)
        desc_col = next((c for c in day.columns if c.upper() == "DESCRIPTION"), None)
        rev_col = next((c for c in day.columns if "NET SALES" in c.upper()), None)
        deal_col = next((c for c in day.columns if "DEAL" in c.upper()), None)
        price_col = next((c for c in day.columns if c.upper() == "PRICE"), None)
        if not upc_col or not qty_col:
            continue

        day["upc"] = day[upc_col].astype(str).str.strip()
        day = day[day["upc"].notna() & (day["upc"] != "") & (day["upc"].str.upper() != "NAN")]
        day["quantity"] = pd.to_numeric(day[qty_col], errors="coerce").fillna(0)
        day["revenue"] = day[rev_col].apply(_parse_money) if rev_col else 0.0
        day["list_price"] = day[price_col].apply(_parse_money) if price_col else 0.0
        day["on_promotion"] = (
            day[deal_col].notna() & (day[deal_col].astype(str).str.strip() != "")
            if deal_col
            else False
        )
        day["date"] = pd.Timestamp(sale_date)
        if desc_col:
            day["description"] = day[desc_col].astype(str)

        for _, r in day.iterrows():
            if r["quantity"] <= 0:
                continue
            qty = float(r["quantity"])
            rev = float(r["revenue"])
            list_price = float(r["list_price"])
            unit_paid = rev / qty if qty > 0 else 0.0
            discount_pct = (
                max(0.0, (1 - unit_paid / list_price) * 100) if list_price > 0 else 0.0
            )
            rows.append(
                {
                    "date": r["date"],
                    "upc": r["upc"],
                    "quantity": qty,
                    "revenue": rev,
                    "list_price": list_price,
                    "discount_pct": discount_pct,
                    "transactions": 1,
                    "on_promotion": bool(r["on_promotion"]),
                    "description": r.get("description", ""),
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "date", "upc", "description", "quantity", "revenue",
                "list_price", "discount_pct", "transactions", "on_promotion",
            ]
        )

    out = pd.DataFrame(rows)
    return (
        out.groupby(["date", "upc", "description"], as_index=False)
        .agg(
            quantity=("quantity", "sum"),
            revenue=("revenue", "sum"),
            list_price=("list_price", "max"),
            discount_pct=("discount_pct", "mean"),
            transactions=("transactions", "sum"),
            on_promotion=("on_promotion", "max"),
        )
        .sort_values(["date", "upc"])
        .reset_index(drop=True)
    )


def load_sales_detailed(
    sales_dir: Path | None = None,
    *,
    default_year: int = 2026,
    start_date: date | None = None,
    end_date: date | None = None,
    use_db: bool | None = None,
) -> pd.DataFrame:
    """Load daily sales — CSV POS exports win over sandbox DB (newer pasted files)."""
    csv_df = _load_sales_detailed_from_csv(
        sales_dir, default_year=default_year, start_date=start_date, end_date=end_date
    )

    if sales_dir is not None:
        return csv_df

    if use_db is None:
        from database.readers.sandbox_data_reader import sandbox_db_available

        use_db = sandbox_db_available()

    if use_db:
        try:
            from database.readers.sandbox_data_reader import get_sandbox_reader

            db_df = get_sandbox_reader().load_sales_detailed(
                start_date=start_date, end_date=end_date
            )
            if not db_df.empty and csv_df.empty:
                return db_df
            if not db_df.empty and not csv_df.empty:
                # POS CSV wins on overlapping date+upc; keeps newly pasted sales days.
                db_df = db_df.copy()
                csv_df = csv_df.copy()
                db_df["date"] = pd.to_datetime(db_df["date"]).dt.normalize()
                csv_df["date"] = pd.to_datetime(csv_df["date"]).dt.normalize()
                db_df["upc"] = db_df["upc"].astype(str).str.strip()
                csv_df["upc"] = csv_df["upc"].astype(str).str.strip()
                keys = csv_df[["date", "upc"]].drop_duplicates()
                keys["_from_csv"] = 1
                merged = db_df.merge(keys, on=["date", "upc"], how="left")
                db_only = merged[merged["_from_csv"].isna()].drop(columns=["_from_csv"])
                return (
                    pd.concat([db_only, csv_df], ignore_index=True)
                    .sort_values(["date", "upc"])
                    .reset_index(drop=True)
                )
        except Exception:
            pass

    return csv_df


def load_daily_sales(sales_dir: Path | None = None, *, default_year: int = 2026, use_db: bool | None = None) -> pd.DataFrame:
    """
    Load all daily Product Sales into one row per UPC × date.
    """
    detailed = load_sales_detailed(
        sales_dir, default_year=default_year, use_db=use_db
    )
    if detailed.empty:
        return pd.DataFrame(columns=["date", "upc", "quantity", "description"])
    return (
        detailed.groupby(["date", "upc", "description"], as_index=False)["quantity"]
        .sum()
        .sort_values(["date", "upc"])
        .reset_index(drop=True)
    )


@lru_cache(maxsize=1)
def _load_daily_sales_cached(default_year: int = 2026) -> pd.DataFrame:
    return load_daily_sales(default_year=default_year)


@lru_cache(maxsize=1)
def build_enriched_sales_cached(*, default_year: int = 2026) -> pd.DataFrame:
    """Cached sales + calendar + weather (expensive — reused across dashboard tabs)."""
    return build_enriched_sales(default_year=default_year)


@lru_cache(maxsize=1)
def sales_date_span(*, default_year: int = 2026) -> tuple[date | None, date | None, int]:
    """
    Return (first_sale_date, last_sale_date, span_days) from POS sales history.

    span_days is inclusive (first → last) and is the max lookback the UI can use.
    """
    daily = _load_daily_sales_cached(default_year=default_year)
    if daily.empty or "date" not in daily.columns:
        return None, None, 30
    start = pd.to_datetime(daily["date"]).min().date()
    end = pd.to_datetime(daily["date"]).max().date()
    span = max(int((end - start).days) + 1, 1)
    return start, end, span


def build_enriched_sales(
    sales: pd.DataFrame | None = None,
    *,
    default_year: int = 2026,
) -> pd.DataFrame:
    """Sales with calendar + Okemos weather joined by date."""
    from v2.forecasting.calendar_enrichment import merge_calendar_features
    from v2.forecasting.weather_enrichment import merge_weather_features

    daily = sales if sales is not None else _load_daily_sales_cached(default_year=default_year)
    if daily.empty:
        return daily

    start = daily["date"].min().date()
    end = daily["date"].max().date()
    enriched = merge_calendar_features(daily, "date", year=default_year)
    enriched = merge_weather_features(enriched, start, end, "date")
    return enriched
