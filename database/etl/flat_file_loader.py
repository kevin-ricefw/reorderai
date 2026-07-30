"""Load CSV/Excel flat files into sandbox tables (exact production names)."""

from __future__ import annotations

import uuid
from typing import Any

import pandas as pd

from app.dashboard.pos_data_service import _load_inventory_from_csv, _load_sales_detailed_from_csv
from app.dashboard.product_normalization import norm_name
from app.dashboard.vendor_catalog_loader import (
    _load_delivery_schedule_cached,
    _load_vendor_catalog_cached,
    get_all_store_vendors,
)
from config.data_paths import SCHEDULE_PATH
from database.connectors.wecomm import SandboxDatabaseConnector
from database.etl.column_mapper import (
    INVENTORY_ALIASES,
    apply_mapping,
    best_product_table,
    fill_not_null_columns,
    fill_required_keys,
    match_columns,
    primary_keys_for_table,
    table_columns,
)
from database.sandbox.naming import SANDBOX_SCHEMA, sandbox_table_name


class FlatFileSandboxLoader:
    """Map project flat files into production-named sandbox tables."""

    def __init__(
        self,
        connector: SandboxDatabaseConnector | None = None,
        *,
        schema: str = SANDBOX_SCHEMA,
    ) -> None:
        self._connector = connector or SandboxDatabaseConnector()
        self._schema = schema

    def _truncate(self, table: str) -> None:
        self._connector.execute(f'TRUNCATE TABLE "{self._schema}"."{table}" CASCADE')

    def _collect_all_vendor_names(self, inventory_df: pd.DataFrame) -> list[str]:
        names: set[str] = set()
        for meta in get_all_store_vendors():
            names.update(str(n).strip() for n in meta.get("inventory_names", []))
        try:
            sched = _load_delivery_schedule_cached(str(SCHEDULE_PATH))
            if not sched.empty and "vendor_name" in sched.columns:
                names.update(sched["vendor_name"].dropna().astype(str).str.strip())
        except Exception:
            pass
        names.update(inventory_df["vendor_name"].dropna().astype(str).str.strip())
        return sorted(n for n in names if n and n.upper() not in ("UNKNOWN", "NAN", ""))

    def load_inventory_to_products(
        self,
        catalog: dict[str, Any],
        inventory_df: pd.DataFrame,
    ) -> tuple[str, int]:
        match = best_product_table(catalog)
        if not match:
            return "products", 0

        prod_schema, prod_table = match
        table = sandbox_table_name(prod_table)
        target_cols = table_columns(catalog, prod_schema, prod_table)
        cols_meta = [
            c for c in catalog.get("columns", [])
            if c["TABLE_SCHEMA"] == prod_schema and c["TABLE_NAME"] == prod_table
        ]
        mapping = match_columns(inventory_df, target_cols, INVENTORY_ALIASES)
        payload = apply_mapping(inventory_df, mapping, target_cols)
        if "min_on_hand" in payload.columns:
            payload["min_on_hand"] = payload["min_on_hand"].fillna(inventory_df["QuantityOnHand"])
        if "price" in payload.columns:
            payload["price"] = payload["price"].fillna(inventory_df["normal_price"])
        pk_cols = primary_keys_for_table(catalog, prod_schema, prod_table)
        payload = fill_required_keys(payload, pk_cols)
        payload = fill_not_null_columns(payload, cols_meta)
        not_null = [c["COLUMN_NAME"] for c in cols_meta if str(c.get("IS_NULLABLE", "YES")).upper() == "NO"]
        optional = [c for c in payload.columns if c not in not_null and payload[c].notna().any()]
        keep = list(dict.fromkeys(not_null + optional))
        payload = payload[[c for c in keep if c in payload.columns]]
        self._truncate(table)
        return table, self._connector.write_dataframe(payload, table, schema=self._schema, chunksize=500)

    def load_vendors(
        self,
        inventory_df: pd.DataFrame,
        catalog: dict[str, Any],
    ) -> tuple[str, int, dict[str, int]]:
        table = "vendors"
        prod_schema = "dbo"
        names = self._collect_all_vendor_names(inventory_df)
        if not names:
            return table, 0, {}

        cols_meta = [c for c in catalog.get("columns", []) if c["TABLE_SCHEMA"] == prod_schema and c["TABLE_NAME"] == table]
        target_cols = table_columns(catalog, prod_schema, table)
        src = pd.DataFrame({"name": names, "source": "pos_import"})
        mapping = match_columns(src, target_cols, {"name": ("name",), "source": ("source",)})
        payload = apply_mapping(src, mapping, target_cols)
        pk_cols = primary_keys_for_table(catalog, prod_schema, table)
        payload = fill_required_keys(payload, pk_cols, start_id=1)
        payload = fill_not_null_columns(payload, cols_meta)
        not_null = [c["COLUMN_NAME"] for c in cols_meta if str(c.get("IS_NULLABLE", "YES")).upper() == "NO"]
        optional = [c for c in payload.columns if c not in not_null and payload[c].notna().any()]
        keep = list(dict.fromkeys(not_null + optional))
        payload = payload[[c for c in keep if c in payload.columns]]
        self._truncate(table)
        n = self._connector.write_dataframe(payload, table, schema=self._schema)
        name_to_id = dict(zip(payload["name"].astype(str).str.strip(), payload["id"]))
        return table, n, name_to_id

    def _product_lookups(self) -> tuple[dict[str, int], dict[str, int]]:
        products = self._connector.read_sql(
            f'SELECT id, product_barcode, sku, name, description FROM "{self._schema}"."products"'
        )
        upc_to_id: dict[str, int] = {}
        name_to_id: dict[str, int] = {}
        for _, r in products.iterrows():
            pid = int(r["id"])
            for col in ("product_barcode", "sku"):
                val = str(r.get(col, "")).strip()
                if val and val.upper() != "NAN":
                    upc_to_id[val] = pid
            for col in ("name", "description"):
                val = norm_name(str(r.get(col, "")))
                if val:
                    name_to_id[val] = pid
        return upc_to_id, name_to_id

    def load_product_vendor_links(
        self,
        catalog: dict[str, Any],
        inventory_df: pd.DataFrame,
        vendor_name_to_id: dict[str, int],
    ) -> tuple[str, int]:
        """Load vendor ↔ product links into product_vendor with vendor_id + product_id."""
        table = "product_vendor"
        prod_schema = "dbo"
        upc_to_id, name_to_id = self._product_lookups()

        rows: list[dict[str, Any]] = []
        seen: set[tuple[int, int]] = set()

        def add_link(vendor_name: str, product_id: int | None, price: float | None) -> None:
            if not product_id:
                return
            vid = vendor_name_to_id.get(vendor_name.strip())
            if not vid:
                return
            key = (vid, product_id)
            if key in seen:
                return
            seen.add(key)
            rows.append({"vendor_id": vid, "product_id": product_id, "price": price or 0})

        # Inventory: every product row has vendor + upc + cost
        for _, r in inventory_df.iterrows():
            vname = str(r.get("vendor_name", "")).strip()
            upc = str(r.get("upc", "")).strip()
            pid = upc_to_id.get(upc)
            cost = pd.to_numeric(r.get("cost"), errors="coerce")
            add_link(vname, pid, float(cost) if pd.notna(cost) else 0)

        # Vendor Excel catalogs
        for meta in get_all_store_vendors():
            vendor_name = meta["inventory_names"][0]
            try:
                cat = _load_vendor_catalog_cached(meta["key"])
            except Exception:
                continue
            if cat is None or cat.empty:
                continue
            for _, r in cat.iterrows():
                pname = norm_name(str(r.get("product_name", "")))
                pid = name_to_id.get(pname)
                if pid is None:
                    # partial match
                    for nkey, nid in name_to_id.items():
                        if pname and (pname in nkey or nkey in pname):
                            pid = nid
                            break
                add_link(vendor_name, pid, None)

        if not rows:
            return table, 0

        cols_meta = [c for c in catalog.get("columns", []) if c["TABLE_SCHEMA"] == prod_schema and c["TABLE_NAME"] == table]
        target_cols = table_columns(catalog, prod_schema, table)
        src = pd.DataFrame(rows)
        mapping = match_columns(
            src,
            target_cols,
            {
                "vendor_id": ("vendor_id",),
                "product_id": ("product_id",),
                "price": ("price",),
            },
        )
        payload = apply_mapping(src, mapping, target_cols)
        pk_cols = primary_keys_for_table(catalog, prod_schema, table)
        payload = fill_required_keys(payload, pk_cols, start_id=1)
        payload = fill_not_null_columns(payload, cols_meta)
        not_null = [c["COLUMN_NAME"] for c in cols_meta if str(c.get("IS_NULLABLE", "YES")).upper() == "NO"]
        optional = [c for c in payload.columns if c not in not_null and payload[c].notna().any()]
        keep = list(dict.fromkeys(not_null + optional))
        payload = payload[[c for c in keep if c in payload.columns]]
        self._truncate(table)
        return table, self._connector.write_dataframe(payload, table, schema=self._schema, chunksize=500)

    def load_sales_to_order_items(self, catalog: dict[str, Any]) -> tuple[str, int]:
        orders_table = "orders"
        items_table = "order_items"
        prod_schema = "dbo"
        sales = _load_sales_detailed_from_csv()
        if sales.empty:
            return items_table, 0

        upc_to_id, _ = self._product_lookups()
        if not upc_to_id:
            return items_table, 0

        orders_cols = table_columns(catalog, prod_schema, orders_table)
        orders_meta = [c for c in catalog.get("columns", []) if c["TABLE_SCHEMA"] == prod_schema and c["TABLE_NAME"] == orders_table]
        items_meta = [c for c in catalog.get("columns", []) if c["TABLE_SCHEMA"] == prod_schema and c["TABLE_NAME"] == items_table]

        self._truncate(items_table)
        self._truncate(orders_table)

        order_rows: list[dict[str, Any]] = []
        item_rows: list[dict[str, Any]] = []
        order_id = 1
        item_id = 1

        for sale_date, day_df in sales.groupby("date"):
            qty_day = day_df["quantity"].sum()
            rev_day = day_df["revenue"].sum()
            order_row: dict[str, Any] = {
                "id": order_id,
                "uuid": str(uuid.uuid4()),
                "user_id": 1,
                "total": float(rev_day),
                "subtotal": float(rev_day),
                "surcharge": 0,
                "status": "completed",
                "type": "pos_import",
                "others_belahf": "",
            }
            if "created_at" in orders_cols:
                order_row["created_at"] = pd.Timestamp(sale_date)
            if "updated_at" in orders_cols:
                order_row["updated_at"] = pd.Timestamp(sale_date)
            order_rows.append(order_row)

            for _, r in day_df.iterrows():
                upc = str(r["upc"]).strip()
                pid = upc_to_id.get(upc)
                if pid is None:
                    continue
                qty = float(r.get("quantity", 0))
                price = float(r.get("list_price") or (r.get("revenue", 0) / qty if qty else 0))
                item_rows.append(
                    {
                        "id": item_id,
                        "order_id": order_id,
                        "product_id": pid,
                        "quantity": int(round(qty)),
                        "fulfilled_quantity": int(round(qty)),
                        "returned_quantity": 0,
                        "price": price,
                        "total": price * qty,
                        "surcharge": 0,
                        "is_backordered": "false",
                        "is_group_order": "false",
                        "is_restricted": False,
                        "is_approved": True,
                    }
                )
                item_id += 1
            order_id += 1

        if not item_rows:
            return items_table, 0

        orders_df = pd.DataFrame(order_rows)
        orders_df = fill_not_null_columns(orders_df, orders_meta)
        orders_df = orders_df[[c for c in orders_df.columns if c in orders_cols]]
        self._connector.write_dataframe(orders_df, orders_table, schema=self._schema, chunksize=500)

        items_df = pd.DataFrame(item_rows)
        items_df = fill_required_keys(items_df, primary_keys_for_table(catalog, prod_schema, items_table), start_id=1)
        items_df = fill_not_null_columns(items_df, items_meta)
        not_null = [c["COLUMN_NAME"] for c in items_meta if str(c.get("IS_NULLABLE", "YES")).upper() == "NO"]
        optional = [c for c in items_df.columns if c not in not_null and items_df[c].notna().any()]
        keep = list(dict.fromkeys(not_null + optional))
        items_df = items_df[[c for c in keep if c in items_df.columns]]
        n = self._connector.write_dataframe(items_df, items_table, schema=self._schema, chunksize=500)
        return items_table, n

    def load_all(self, catalog: dict[str, Any]) -> dict[str, int]:
        results: dict[str, int] = {}
        inv = _load_inventory_from_csv()

        table, n = self.load_inventory_to_products(catalog, inv)
        results[f"{self._schema}.{table}"] = n

        table, n, vendor_map = self.load_vendors(inv, catalog)
        results[f"{self._schema}.{table}"] = n

        table, n = self.load_product_vendor_links(catalog, inv, vendor_map)
        results[f"{self._schema}.{table}"] = n

        table, n = self.load_sales_to_order_items(catalog)
        results[f"{self._schema}.{table}"] = n

        return results
