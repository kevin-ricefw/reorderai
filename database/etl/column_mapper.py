"""Map flat-file columns to production/sandbox table columns."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

# Inventory CSV column -> common production column aliases
INVENTORY_ALIASES: dict[str, tuple[str, ...]] = {
    "upc": ("upc", "barcode", "sku", "itemcode", "item_code", "productcode", "product_barcode"),
    "description": ("description", "name", "productname", "product_name", "itemname", "item_name"),
    "name": ("description", "name", "productname", "product_name"),
    "cost": ("cost", "unitcost", "unit_cost", "purchaseprice", "purchase_price", "case_cost"),
    "purchase_price": ("cost", "case_cost", "purchaseprice", "purchase_price"),
    "normal_price": ("normal_price", "price", "retailprice", "retail_price", "sellprice"),
    "price": ("normal_price", "price", "retailprice", "retail_price"),
    "QuantityOnHand": ("quantityonhand", "onhand", "on_hand", "qtyonhand", "stock", "quantity"),
    "min_on_hand": ("quantityonhand", "onhand", "on_hand", "qtyonhand", "stock", "quantity"),
    "vendor_name": ("vendor_name", "vendor", "supplier", "suppliername"),
    "dept_name": ("dept_name", "department", "departmentname"),
    "section_name": ("section_name", "section", "category", "categoryname"),
    "pack": ("pack", "packsize", "pack_size"),
    "case_cost": ("case_cost", "casecost"),
    "active": ("active", "isactive", "is_active"),
    "is_active": ("active", "isactive", "is_active"),
    "LowInventoryAlert": ("lowinventoryalert", "reorderpoint", "reorder_point", "rop", "min_reorder_quantity"),
    "ReOrderQuantity": ("reorderquantity", "reorder_qty", "reorderquantity", "ordered_quantity"),
    "product_barcode": ("upc", "barcode", "product_barcode"),
    "sku": ("upc", "sku", "productcode"),
}

SALES_ALIASES: dict[str, tuple[str, ...]] = {
    "upc": ("upc", "barcode", "sku", "itemcode"),
    "description": ("description", "name", "productname"),
    "quantity": ("quantity", "qty", "qtysold", "qty_sold", "units"),
    "revenue": ("revenue", "netsales", "net_sales", "sales", "amount"),
    "cost": ("cost", "unitcost", "unit_cost"),
    "list_price": ("list_price", "price", "unitprice"),
    "date": ("date", "saledate", "sale_date", "orderdate", "transactiondate"),
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def match_columns(source_df: pd.DataFrame, target_columns: list[str], aliases: dict[str, tuple[str, ...]]) -> dict[str, str]:
    """Return mapping target_col -> source_col."""
    src_norm = {_norm(c): c for c in source_df.columns}
    tgt_norm = {_norm(c): c for c in target_columns}
    mapping: dict[str, str] = {}

    for canonical, alias_list in aliases.items():
        for alias in alias_list:
            if alias in src_norm and canonical in tgt_norm:
                mapping[tgt_norm[canonical]] = src_norm[alias]
                break
            if alias in src_norm:
                # target might use different casing
                for tc in target_columns:
                    if _norm(tc) == _norm(canonical):
                        mapping[tc] = src_norm[alias]
                        break

    # Direct name matches
    for tc in target_columns:
        if tc in mapping:
            continue
        tn = _norm(tc)
        if tn in src_norm:
            mapping[tc] = src_norm[tn]

    return mapping


def apply_mapping(df: pd.DataFrame, mapping: dict[str, str], target_columns: list[str]) -> pd.DataFrame:
    seen: set[str] = set()
    unique_targets: list[str] = []
    for col in target_columns:
        if col not in seen:
            seen.add(col)
            unique_targets.append(col)

    out = pd.DataFrame(index=df.index)
    for col in unique_targets:
        src = mapping.get(col)
        out[col] = df[src] if src and src in df.columns else None
    return out


def _default_for_column(col: dict[str, Any], *, now: Any) -> Any:
    data_type = str(col.get("DATA_TYPE", "varchar")).lower()
    default = col.get("COLUMN_DEFAULT")
    if default not in (None, ""):
        cleaned = str(default).strip("()")
        if cleaned.isdigit():
            return int(cleaned)
        if cleaned.replace(".", "", 1).isdigit():
            return float(cleaned)
        if cleaned.upper() in ("GETDATE()", "SYSDATETIME()", "NOW()"):
            return now

    if data_type in ("int", "bigint", "smallint", "tinyint", "decimal", "numeric", "float", "real", "money"):
        return 0
    if data_type == "bit":
        return False
    if data_type in ("datetime", "datetime2", "date", "smalldatetime"):
        return now
    if data_type == "uuid" or col.get("COLUMN_NAME", "").lower() == "uuid":
        import uuid

        return str(uuid.uuid4())
    return ""


def fill_not_null_columns(
    df: pd.DataFrame,
    columns_meta: list[dict[str, Any]],
) -> pd.DataFrame:
    """Fill required NOT NULL columns missing from flat-file mapping."""
    import uuid
    from datetime import datetime, timezone

    out = df.copy()
    now = datetime.now(timezone.utc)

    for col in columns_meta:
        if str(col.get("IS_NULLABLE", "YES")).upper() != "NO":
            continue
        name = col["COLUMN_NAME"]
        lower = name.lower()

        if name in out.columns and out[name].notna().all():
            continue

        if lower == "uuid" or str(col.get("DATA_TYPE", "")).lower() == "uniqueidentifier":
            out[name] = [str(uuid.uuid4()) for _ in range(len(out))]
        elif lower == "slug" and "name" in out.columns:
            out[name] = (
                out["name"]
                .astype(str)
                .str.lower()
                .str.replace(r"[^a-z0-9]+", "-", regex=True)
                .str.strip("-")
            )
        elif lower in ("created_at", "updated_at"):
            out[name] = now
        elif lower == "name" and "description" in out.columns:
            out[name] = out["description"]
        elif lower == "description" and "name" in out.columns:
            out[name] = out["name"]
        elif lower in ("is_active", "active"):
            out[name] = True
        elif name in out.columns:
            out[name] = out[name].fillna(_default_for_column(col, now=now))
        else:
            default = _default_for_column(col, now=now)
            if lower == "uuid":
                out[name] = [str(uuid.uuid4()) for _ in range(len(out))]
            elif isinstance(default, str) and default == "":
                out[name] = ""
            elif isinstance(default, bool):
                out[name] = default
            else:
                out[name] = default

    return out


def fill_required_keys(
    df: pd.DataFrame,
    pk_cols: list[str],
    *,
    start_id: int = 1,
) -> pd.DataFrame:
    """Populate missing primary-key values for sandbox inserts."""
    out = df.copy()
    if not pk_cols:
        return out

    if len(pk_cols) == 1 and pk_cols[0].lower() == "id":
        if out[pk_cols[0]].isna().all() if pk_cols[0] in out.columns else True:
            if pk_cols[0] not in out.columns:
                out[pk_cols[0]] = range(start_id, start_id + len(out))
            else:
                missing = out[pk_cols[0]].isna()
                out.loc[missing, pk_cols[0]] = range(start_id, start_id + int(missing.sum()))
        return out

    for i, col in enumerate(pk_cols):
        if col not in out.columns or out[col].isna().all():
            if col.lower() in ("id", f"{col.lower()}"):
                out[col] = range(start_id, start_id + len(out))
            elif col.lower() == "uuid":
                import uuid

                out[col] = [str(uuid.uuid4()) for _ in range(len(out))]
            else:
                out[col] = [f"{col}_{start_id + j}" for j in range(len(out))]
    return out


def best_product_table(catalog: dict[str, Any]) -> tuple[str, str] | None:
    """Find production table best matching inventory/products."""
    candidates = []
    for tbl in catalog.get("tables", []):
        name = tbl["TABLE_NAME"].lower()
        if not any(k in name for k in ("item", "product", "inventory", "sku")):
            continue
        cols = [
            c["COLUMN_NAME"].lower()
            for c in catalog.get("columns", [])
            if c["TABLE_NAME"] == tbl["TABLE_NAME"]
        ]
        score = 0
        if any("upc" in c or "barcode" in c or "sku" in c for c in cols):
            score += 3
        if any("description" in c or "name" in c for c in cols):
            score += 2
        if any("cost" in c or "price" in c for c in cols):
            score += 2
        if any("quantity" in c or "onhand" in c or "on_hand" in c for c in cols):
            score += 1
        if name in ("items", "products", "product"):
            score += 2
        candidates.append((score, tbl["TABLE_SCHEMA"], tbl["TABLE_NAME"]))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    _, schema, table = candidates[0]
    return schema, table


def primary_keys_for_table(catalog: dict[str, Any], schema: str, table: str) -> list[str]:
    pks = [
        pk["COLUMN_NAME"]
        for pk in sorted(
            [
                p
                for p in catalog.get("primary_keys", [])
                if p["TABLE_SCHEMA"] == schema and p["TABLE_NAME"] == table
            ],
            key=lambda x: x.get("ORDINAL_POSITION", 0),
        )
    ]
    return pks


def table_columns(catalog: dict[str, Any], schema: str, table: str) -> list[str]:
    return [
        c["COLUMN_NAME"]
        for c in catalog.get("columns", [])
        if c["TABLE_SCHEMA"] == schema and c["TABLE_NAME"] == table
    ]
