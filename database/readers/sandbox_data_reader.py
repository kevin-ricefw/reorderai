"""Read dashboard-ready DataFrames from PostgreSQL sandbox (production table names)."""

from __future__ import annotations

from datetime import date
from functools import lru_cache

import pandas as pd

from database.connectors.wecomm import SandboxDatabaseConnector
from database.sandbox.naming import SANDBOX_SCHEMA

SCHEMA = SANDBOX_SCHEMA


class SandboxDataReader:
    def __init__(self, connector: SandboxDatabaseConnector | None = None) -> None:
        self._connector = connector or SandboxDatabaseConnector()

    def _table_exists(self, table: str) -> bool:
        df = self._connector.read_sql(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :table
            LIMIT 1
            """,
            {"schema": SCHEMA, "table": table},
        )
        return not df.empty

    def _row_count(self, table: str) -> int:
        if not self._table_exists(table):
            return 0
        df = self._connector.read_sql(f'SELECT COUNT(*) AS n FROM "{SCHEMA}"."{table}"')
        return int(df.iloc[0]["n"])

    def is_available(self) -> bool:
        try:
            return self._row_count("products") > 0 or self._row_count("order_items") > 0
        except Exception:
            return False

    def load_inventory(self) -> pd.DataFrame:
        if self._row_count("products") > 0:
            # products table has no vendor column — join product_vendor + vendors
            df = self._connector.read_sql(
                f"""
                SELECT p.*, v.name AS vendor_name
                FROM "{SCHEMA}"."products" p
                LEFT JOIN LATERAL (
                    SELECT pv.vendor_id
                    FROM "{SCHEMA}"."product_vendor" pv
                    WHERE pv.product_id = p.id
                    ORDER BY pv.id
                    LIMIT 1
                ) pv ON TRUE
                LEFT JOIN "{SCHEMA}"."vendors" v ON v.id = pv.vendor_id
                """
            )
            out = _products_to_inventory(df)
            return out
        return pd.DataFrame()

    def load_sales_detailed(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        if not self._table_exists("order_items") or not self._table_exists("products"):
            return pd.DataFrame()

        q = f"""
        SELECT
            o.created_at AS date,
            oi.quantity,
            oi.price AS list_price,
            COALESCE(p.product_barcode, p.sku) AS upc,
            COALESCE(p.description, p.name) AS description
        FROM "{SCHEMA}"."order_items" oi
        JOIN "{SCHEMA}"."orders" o ON o.id = oi.order_id
        JOIN "{SCHEMA}"."products" p ON p.id = oi.product_id
        WHERE o.created_at IS NOT NULL
        """
        df = self._connector.read_sql(q)
        if df.empty:
            # Fallback if orders lack created_at
            q = f"""
            SELECT
                oi.quantity,
                oi.price AS list_price,
                COALESCE(p.product_barcode, p.sku) AS upc,
                COALESCE(p.description, p.name) AS description,
                oi.order_id
            FROM "{SCHEMA}"."order_items" oi
            JOIN "{SCHEMA}"."products" p ON p.id = oi.product_id
            """
            df = self._connector.read_sql(q)
            if df.empty:
                return df
            df["date"] = pd.to_datetime("2026-01-07") + pd.to_timedelta(df["order_id"], unit="D")

        df["date"] = pd.to_datetime(df["date"])
        df["upc"] = df["upc"].astype(str).str.strip()
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
        df["list_price"] = pd.to_numeric(df["list_price"], errors="coerce").fillna(0)
        df["revenue"] = df["quantity"] * df["list_price"]
        df["discount_pct"] = 0.0
        df["transactions"] = 1
        df["on_promotion"] = False

        if start_date:
            df = df[df["date"] >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df["date"] <= pd.Timestamp(end_date)]

        return df[
            ["date", "upc", "description", "quantity", "revenue", "list_price", "discount_pct", "transactions", "on_promotion"]
        ].reset_index(drop=True)

    def load_delivery_schedule(self) -> pd.DataFrame:
        # No production table for Excel schedule — return empty so dashboard falls back to CSV
        return pd.DataFrame()

    def load_vendor_catalog(self, vendor_key: str) -> pd.DataFrame:
        if not self._table_exists("product_vendor"):
            return pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])
        meta = None
        for v in __import__(
            "app.dashboard.vendor_catalog_loader", fromlist=["get_all_store_vendors"]
        ).get_all_store_vendors():
            if v["key"] == vendor_key:
                meta = v
                break
        vendor_name = meta["inventory_names"][0] if meta else vendor_key
        df = self._connector.read_sql(
            f"""
            SELECT p.name AS product_name, pv.price
            FROM "{SCHEMA}"."product_vendor" pv
            JOIN "{SCHEMA}"."vendors" v ON v.id = pv.vendor_id
            JOIN "{SCHEMA}"."products" p ON p.id = pv.product_id
            WHERE UPPER(v.name) = UPPER(:vendor_name)
            ORDER BY p.name
            """,
            {"vendor_name": vendor_name},
        )
        if df.empty:
            return pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])
        out = pd.DataFrame()
        out["product_name"] = df["product_name"].astype(str).str.strip()
        out["unit"] = "Case"
        out["catalog_pack"] = pd.to_numeric(df.get("price"), errors="coerce")
        return out.drop_duplicates(subset=["product_name"])

    def load_waste_records(self) -> pd.DataFrame:
        return pd.DataFrame()


def _products_to_inventory(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["upc"] = df.get("product_barcode", df.get("sku", "")).astype(str).str.strip()
    out["description"] = df.get("description", df.get("name", "")).astype(str).str.strip()
    out["cost"] = pd.to_numeric(df.get("purchase_price"), errors="coerce")
    out["normal_price"] = pd.to_numeric(df.get("price"), errors="coerce")
    # ETL maps POS QuantityOnHand → min_on_hand (production column name)
    out["QuantityOnHand"] = pd.to_numeric(df.get("min_on_hand"), errors="coerce")
    if "vendor_name" in df.columns:
        out["vendor_name"] = df["vendor_name"].fillna("Unknown").astype(str).str.strip()
    else:
        out["vendor_name"] = "Unknown"
    out["pack"] = pd.to_numeric(df.get("pack"), errors="coerce") if "pack" in df.columns else pd.NA
    out["case_cost"] = pd.to_numeric(df.get("case_cost"), errors="coerce") if "case_cost" in df.columns else pd.NA
    active = df.get("is_active", True)
    out["active"] = active.fillna(True).astype(bool) if hasattr(active, "fillna") else True
    out["LowInventoryAlert"] = pd.to_numeric(df.get("min_reorder_quantity"), errors="coerce") if "min_reorder_quantity" in df.columns else pd.NA
    out["ReOrderQuantity"] = pd.to_numeric(df.get("ordered_quantity"), errors="coerce") if "ordered_quantity" in df.columns else pd.NA
    return out


@lru_cache(maxsize=1)
def get_sandbox_reader() -> SandboxDataReader:
    return SandboxDataReader()


@lru_cache(maxsize=1)
def sandbox_db_available() -> bool:
    try:
        return get_sandbox_reader().is_available()
    except Exception:
        return False
