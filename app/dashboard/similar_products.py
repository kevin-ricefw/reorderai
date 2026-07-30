"""Detect same item across brands/vendors to avoid overstocking duplicates."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.dashboard.product_normalization import product_signature


def _stock_int(val: Any) -> int:
    try:
        return int(round(float(val or 0)))
    except (TypeError, ValueError):
        return 0


def _fmt_alt(description: str, stock: Any) -> str:
    """UI format: item name + count only."""
    return f"{str(description).strip()}: {_stock_int(stock)}"


def build_similar_product_groups(
    inventory: pd.DataFrame,
    *,
    min_group_size: int = 2,
    name_col: str = "description",
    size_col: str = "size",
    vendor_col: str = "vendor_name",
    upc_col: str = "upc",
    stock_col: str = "QuantityOnHand",
) -> pd.DataFrame:
    """
    Group inventory rows that are the same product (brand-agnostic) from different SKUs.

    Groups by core item name (brand/frozen stripped). Size differences still group together
    so freezer stock of Methi Leaves 300G vs 310G is visible side by side.
    """
    empty_cols = [
        "signature_key",
        "group_label",
        "member_count",
        "member_upcs",
        "alternatives_text",
        "total_on_hand",
    ]
    if inventory.empty:
        return pd.DataFrame(columns=empty_cols)

    rows: list[dict[str, Any]] = []
    for _, inv in inventory.iterrows():
        sig = product_signature(
            str(inv.get(name_col, "")),
            size_field=str(inv.get(size_col, "") or "") or None,
        )
        key = str(sig.get("item_key") or sig.get("signature_key") or "")
        if not key or len(sig["core_tokens"]) < 2:
            continue
        rows.append(
            {
                "upc": str(inv.get(upc_col, "")).strip(),
                "description": str(inv.get(name_col, "")).strip(),
                "vendor_name": str(inv.get(vendor_col, "")).strip(),
                "brand": sig["brand"],
                "size": sig["size"] or "",
                "signature_key": key,
                "group_label": " ".join(sig["core_tokens"][:6]),
                "on_hand": float(inv.get(stock_col) or 0),
            }
        )

    if not rows:
        return pd.DataFrame(columns=empty_cols)

    df = pd.DataFrame(rows)
    groups = []
    for key, grp in df.groupby("signature_key"):
        if len(grp) < min_group_size:
            continue
        alt_parts = [
            _fmt_alt(r["description"], r["on_hand"])
            for _, r in grp.sort_values("description").iterrows()
        ]
        groups.append(
            {
                "signature_key": key,
                "group_label": grp.iloc[0]["group_label"],
                "member_count": len(grp),
                "member_upcs": ",".join(grp["upc"].tolist()),
                "alternatives_text": " | ".join(alt_parts),
                "total_on_hand": round(grp["on_hand"].sum(), 2),
            }
        )

    if not groups:
        return pd.DataFrame(columns=empty_cols)

    return pd.DataFrame(groups).sort_values("member_count", ascending=False).reset_index(drop=True)


def attach_similar_alternatives(
    product_df: pd.DataFrame,
    similar_groups: pd.DataFrame,
    *,
    inventory_lookup: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add same-item other-brand stock column: 'NAME: count | NAME: count'."""
    if product_df.empty:
        out = product_df.copy()
        out["similar_alternatives"] = ""
        out["similar_group_size"] = 0
        out["other_brands_stock"] = ""
        return out

    out = product_df.copy()
    out["similar_alternatives"] = ""
    out["similar_group_size"] = 0
    out["other_brands_stock"] = ""

    if similar_groups.empty:
        return out

    lookup = inventory_lookup if inventory_lookup is not None else product_df
    lookup = lookup.copy()
    lookup["upc"] = lookup["upc"].astype(str).str.strip()
    lookup = lookup.drop_duplicates(subset=["upc"])

    upc_to_alts: dict[str, tuple[str, int]] = {}
    for _, g in similar_groups.iterrows():
        upcs = [u.strip() for u in str(g["member_upcs"]).split(",") if u.strip()]
        for upc in upcs:
            others = [u for u in upcs if u != upc]
            if not others:
                continue
            alt_rows = lookup[lookup["upc"].isin(others)]
            parts: list[str] = []
            sort_col = "description" if "description" in alt_rows.columns else alt_rows.columns[0]
            for _, r in alt_rows.sort_values(sort_col).iterrows():
                stock = r.get("current_stock", r.get("QuantityOnHand", 0))
                desc = r.get("description", "")
                parts.append(_fmt_alt(desc, stock))
            alt_text = " | ".join(parts) if parts else ""
            if not alt_text:
                own_desc = ""
                own_hit = lookup[lookup["upc"] == upc]
                if not own_hit.empty:
                    own_desc = str(own_hit.iloc[0].get("description", ""))
                fallback_parts = []
                for chunk in str(g["alternatives_text"]).split(" | "):
                    if own_desc and chunk.upper().startswith(own_desc.upper() + ":"):
                        continue
                    if chunk.strip():
                        fallback_parts.append(chunk.strip())
                alt_text = " | ".join(fallback_parts)
            upc_to_alts[upc] = (alt_text, int(g["member_count"]))

    for idx, row in out.iterrows():
        upc = str(row.get("upc", "")).strip()
        if upc in upc_to_alts:
            text, n = upc_to_alts[upc]
            out.at[idx, "similar_alternatives"] = text
            out.at[idx, "other_brands_stock"] = text
            out.at[idx, "similar_group_size"] = n

    return out


def attach_same_item_stock_from_inventory(
    product_df: pd.DataFrame,
    full_inventory: pd.DataFrame,
    *,
    name_col: str = "description",
    size_col: str = "size",
    upc_col: str = "upc",
    stock_col: str = "QuantityOnHand",
) -> pd.DataFrame:
    """
    For each order line, search full inventory for the same item (any brand)
    and attach 'NAME: count | NAME: count' (excluding the row's own UPC).
    """
    if product_df.empty:
        out = product_df.copy()
        out["similar_alternatives"] = ""
        out["other_brands_stock"] = ""
        out["similar_group_size"] = 0
        return out

    inv = full_inventory.copy()
    if inv.empty:
        out = product_df.copy()
        out["similar_alternatives"] = ""
        out["other_brands_stock"] = ""
        out["similar_group_size"] = 0
        return out

    if size_col not in inv.columns:
        inv[size_col] = ""

    by_key: dict[str, list[tuple[str, str, int]]] = {}
    upc_to_key: dict[str, str] = {}
    for _, inv_row in inv.iterrows():
        upc = str(inv_row.get(upc_col, "")).strip()
        desc = str(inv_row.get(name_col, "")).strip()
        if not upc or not desc:
            continue
        sig = product_signature(desc, size_field=str(inv_row.get(size_col, "") or "") or None)
        key = str(sig.get("item_key") or "")
        if not key or len(sig["core_tokens"]) < 2:
            continue
        stock = _stock_int(inv_row.get(stock_col, inv_row.get("current_stock", 0)))
        by_key.setdefault(key, []).append((upc, desc, stock))
        upc_to_key[upc] = key

    out = product_df.copy()
    alts: list[str] = []
    sizes: list[int] = []
    for _, row in out.iterrows():
        upc = str(row.get("upc", "")).strip()
        desc = str(row.get("description", "")).strip()
        key = upc_to_key.get(upc)
        if not key:
            sig = product_signature(desc, size_field=str(row.get("size", "") or "") or None)
            key = str(sig.get("item_key") or "")
        members = by_key.get(key, []) if key else []
        others = [(d, s) for u, d, s in members if u != upc]
        seen: set[str] = set()
        parts: list[str] = []
        for d, s in sorted(others, key=lambda x: x[0].upper()):
            du = d.upper()
            if du in seen:
                continue
            seen.add(du)
            parts.append(_fmt_alt(d, s))
        text = " | ".join(parts)
        alts.append(text)
        sizes.append(len(parts) + 1 if parts else 0)

    out["similar_alternatives"] = alts
    out["other_brands_stock"] = alts
    out["similar_group_size"] = sizes
    return out
