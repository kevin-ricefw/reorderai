"""Exclude loose produce SKUs — keep any product with a package size, weight, or count."""

from __future__ import annotations

import re

import pandas as pd

from v2.analytics.dashboard_constants import DASHBOARD_NOTE

# Package size, weight, count, or numeric quantity in name/size → always keep
PACKAGE_SIZE_PATTERN = re.compile(
    r"(?:"
    r"\d+\s*(?:"
    r"G|GM|GR|GRAM|GRAMS|KG|KILO|KILOS|"
    r"LB|LBS|POUND|POUNDS|"
    r"OZ|OUNCE|OUNCES|"
    r"ML|LTR|LITER|LITRE|L|"
    r"PC|PCS|PIECE|PIECE|PIECES|CT|COUNT|PACK|PK"
    r")\b|"
    r"\d+\s*[xX×]\s*\d+|"
    r"\b\d+\b"
    r")",
    re.IGNORECASE,
)

FROZEN_KEEP = re.compile(r"\bFROZEN\b", re.IGNORECASE)
CANNED_KEEP = re.compile(r"\bCANNED\b", re.IGNORECASE)
PROCESSED_KEEP = re.compile(
    r"\b(?:DRIED|DEHYDRATED|CANNED|PICKLED|PASTE|POWDER|FROZEN|GRATED|SLICED|BLANCHED|ROASTED|TIN)\b",
    re.IGNORECASE,
)

# Simple loose produce names (no size/weight/count) — user-provided examples + store variants
SIMPLE_LOOSE_PRODUCE = re.compile(
    r"^(?:FRESH\s+)?(?:"
    r"DOSAKAI|DOSAKAYA|CHIKOO|EGGPLANT|GINGER|THAI\s+CHILLI|THAI\s+CHILI|"
    r"SWEET\s+POTATO|EDDO|TINDORA|INDIAN\s+EGGPLANT|ROMA\s+TOMATO|"
    r"CURRY\s+LEAVES|METHI\s+LEAVES|DESI\s+OKRA|OKRA|BEET\s*ROOT|"
    r"BURRO\s+BANANA|CHINESE\s+EGGPLANT|DRUMSTICKS?|DRUMSTICK\s+LEAVES|"
    r"FLAT\s+VALOR|GUVAR|GUWAR|BITTER\s*MELON|KARELA|CILANTRO|DILL\s+LEAVES|"
    r"AMLA|CHAYOTE|CAULIFLOWER|CABBAGE|CUSTARD\s+APPLE|PLANTAIN|"
    r"BANANA\s+FLOWER|BANANA\s+STEM|BANANA\s+LEAV|AMARANTH\s+LEAVES|"
    r"GONGURA|GANGURA|MULI|RADISH|SPINACH|PALAK|CHIKOO|CHIKU"
    r")\s*$",
    re.IGNORECASE,
)

# Re-export for backward compatibility
__all__ = ["DASHBOARD_NOTE", "PRODUCE_VENDOR_NAMES", "filter_dataframe_excluding_produce"]

# Fresh produce suppliers — exclude all SKUs from grocery analytics / reorder
PRODUCE_VENDOR_NAMES: frozenset[str] = frozenset({"JALARAM", "CARLOS"})


def is_produce_vendor(vendor_name: str | None) -> bool:
    """True for fresh produce suppliers excluded entirely from analysis."""
    if not vendor_name:
        return False
    return str(vendor_name).strip().upper() in PRODUCE_VENDOR_NAMES


def _combined_label(product_name: str, size: str | None = None) -> str:
    return f"{product_name or ''} {size or ''}".strip()


def _has_package_size(text: str) -> bool:
    return bool(PACKAGE_SIZE_PATTERN.search(text))


def _is_processed_or_branded_keep(product_name: str, department: str | None) -> bool:
    name = (product_name or "").strip()
    dept = (department or "").strip().upper()
    if FROZEN_KEEP.search(name) or "FROZEN" in dept:
        return True
    if CANNED_KEEP.search(name) or "CANNED" in dept:
        return True
    if PROCESSED_KEEP.search(name):
        return True
    return False


def is_loose_fresh_produce(
    product_name: str | None,
    department: str | None = None,
    *,
    size: str | None = None,
) -> bool:
    """
    Return True if this SKU should be EXCLUDED from analysis.

    Keep: any name/size with G, GM, KG, LB, OZ, ML, CT, PCS, PACK, or a number;
    frozen, canned, dried, processed, or branded packaged products.

    Exclude: simple loose produce names only (e.g. Dosakai, Curry Leaves, Fresh Ginger)
    with no package size, weight, count, or packaged-product information.
    """
    name = (product_name or "").strip()
    if not name:
        return False

    combined = _combined_label(name, size)

    if _has_package_size(combined):
        return False

    if _is_processed_or_branded_keep(name, department):
        return False

    normalized = re.sub(r"\s+", " ", name).strip()
    if SIMPLE_LOOSE_PRODUCE.match(normalized):
        return True

    dept = (department or "").strip().upper()
    if dept == "PRODUCE":
        return True

    return False


def should_exclude_from_analysis(
    product_name: str | None,
    department: str | None = None,
    *,
    size: str | None = None,
    vendor_name: str | None = None,
) -> bool:
    """Exclude produce vendors entirely, then loose unweighted produce names."""
    if is_produce_vendor(vendor_name):
        return True
    return is_loose_fresh_produce(product_name, department, size=size)


is_fresh_produce_excluded = is_loose_fresh_produce


def filter_dataframe_excluding_produce(
    df: pd.DataFrame,
    *,
    name_col: str = "description",
    dept_col: str | None = "dept_name",
    size_col: str | None = "size",
    vendor_col: str | None = "vendor_name",
    upc_col: str = "upc",
    inventory: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Drop loose produce rows and all SKUs from produce vendors (JALARAM, CARLOS)."""
    if df.empty:
        return df

    out = df.copy()
    dept_map: dict[str, str] = {}
    size_map: dict[str, str] = {}
    vendor_map: dict[str, str] = {}
    if inventory is not None and upc_col in out.columns:
        inv = inventory.copy()
        inv[upc_col] = inv[upc_col].astype(str).str.strip()
        if dept_col and dept_col in inv.columns:
            dept_map = dict(zip(inv[upc_col], inv[dept_col].fillna("")))
        if size_col and size_col in inv.columns:
            size_map = dict(zip(inv[upc_col], inv[size_col].fillna("")))
        if vendor_col and vendor_col in inv.columns:
            vendor_map = dict(zip(inv[upc_col], inv[vendor_col].fillna("")))

    names = out[name_col] if name_col in out.columns else out.get("product_name", pd.Series("", index=out.index))
    depts = out[dept_col].fillna("") if dept_col and dept_col in out.columns else pd.Series("", index=out.index)
    sizes = out[size_col].fillna("") if size_col and size_col in out.columns else pd.Series("", index=out.index)
    vendors = out[vendor_col].fillna("") if vendor_col and vendor_col in out.columns else pd.Series("", index=out.index)

    if upc_col in out.columns:
        upcs = out[upc_col].astype(str).str.strip()
        if dept_map:
            depts = upcs.map(dept_map).fillna(depts)
        if size_map:
            sizes = upcs.map(size_map).fillna(sizes)
        if vendor_map:
            vendors = upcs.map(vendor_map).fillna(vendors)

    mask = [
        not should_exclude_from_analysis(
            str(n),
            str(d) if d else None,
            size=str(s) if s else None,
            vendor_name=str(v) if v else None,
        )
        for n, d, s, v in zip(names, depts, sizes, vendors)
    ]
    return out[mask].reset_index(drop=True)


def count_excluded(df: pd.DataFrame, **kwargs) -> int:
    before = len(df)
    return before - len(filter_dataframe_excluding_produce(df, **kwargs))
