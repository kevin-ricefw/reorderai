"""Global product search — find which vendors supply a product."""

from __future__ import annotations

import re

import pandas as pd

from app.dashboard.pos_data_service import load_inventory
from app.dashboard.product_normalization import norm_name
from app.dashboard.vendor_catalog_loader import get_all_store_vendors, load_vendor_catalog


def _vendor_display_name(meta: dict) -> str:
    return meta["inventory_names"][0] if meta.get("inventory_names") else meta.get("key", "")


def build_product_vendor_index() -> pd.DataFrame:
    """
    Searchable index: inventory rows + vendor catalog products.

    Columns: product_name, vendor_name, vendor_key, upc, source, unit_cost, on_hand
    """
    rows: list[dict] = []

    inv = load_inventory()
    if not inv.empty:
        for _, r in inv.iterrows():
            desc = str(r.get("description", "")).strip()
            if not desc or desc.upper() == "NAN":
                continue
            rows.append(
                {
                    "product_name": desc,
                    "search_text": norm_name(desc),
                    "vendor_name": str(r.get("vendor_name", "Unknown")).strip(),
                    "vendor_key": "",
                    "upc": str(r.get("upc", "")).strip(),
                    "source": "Inventory (POS)",
                    "unit_cost": float(pd.to_numeric(r.get("cost"), errors="coerce") or 0),
                    "on_hand": float(pd.to_numeric(r.get("QuantityOnHand"), errors="coerce") or 0),
                }
            )

    for meta in get_all_store_vendors():
        vkey = meta["key"]
        vname = _vendor_display_name(meta)
        try:
            cat = load_vendor_catalog(vkey)
        except Exception:
            continue
        if cat.empty or "product_name" not in cat.columns:
            continue
        for _, r in cat.iterrows():
            pname = str(r.get("product_name", "")).strip()
            if not pname or pname.upper() == "NAN":
                continue
            rows.append(
                {
                    "product_name": pname,
                    "search_text": norm_name(pname),
                    "vendor_name": vname,
                    "vendor_key": vkey,
                    "upc": "",
                    "source": "Vendor catalog",
                    "unit_cost": float(pd.to_numeric(r.get("price"), errors="coerce") or 0)
                    if "price" in cat.columns
                    else 0.0,
                    "on_hand": None,
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "product_name",
                "search_text",
                "vendor_name",
                "vendor_key",
                "upc",
                "source",
                "unit_cost",
                "on_hand",
            ]
        )
    return pd.DataFrame(rows)


def _loose_text(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.lower()
        .str.replace(r"[^\w\s]", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def search_products(
    query: str,
    index: pd.DataFrame,
    *,
    limit: int = 200,
) -> pd.DataFrame:
    """Substring search on normalized product name."""
    q = query.strip().lower()
    if len(q) < 2 or index.empty:
        return pd.DataFrame()

    q_clean = re.sub(r"[^\w\s]", "", q)
    tokens = [t for t in q_clean.split() if len(t) >= 2]
    if not tokens:
        tokens = [q_clean] if len(q_clean) >= 2 else [q]

    loose_names = _loose_text(index["product_name"])
    loose_search = _loose_text(index["search_text"])

    mask = pd.Series(True, index=index.index)
    for tok in tokens:
        mask &= loose_search.str.contains(tok, na=False, regex=False) | loose_names.str.contains(
            tok, na=False, regex=False
        )

    hits = index[mask].copy()
    if hits.empty:
        mask = pd.Series(False, index=index.index)
        for tok in tokens:
            mask |= loose_search.str.contains(tok, na=False, regex=False)
            mask |= loose_names.str.contains(tok, na=False, regex=False)
        hits = index[mask].copy()

    return hits.head(limit)


def vendors_for_query(query: str, index: pd.DataFrame) -> pd.DataFrame:
    """Unique vendors that supply products matching the query."""
    hits = search_products(query, index, limit=500)
    if hits.empty:
        return pd.DataFrame(columns=["vendor_name", "vendor_key", "matching_products", "sources"])

    g = (
        hits.groupby(["vendor_name", "vendor_key"], as_index=False)
        .agg(
            matching_products=("product_name", "nunique"),
            sample_products=("product_name", lambda s: " · ".join(sorted(set(s))[:5])),
            sources=("source", lambda s: ", ".join(sorted(set(s)))),
        )
        .sort_values("matching_products", ascending=False)
    )
    return g
