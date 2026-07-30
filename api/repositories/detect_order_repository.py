"""
DB reads for W-1 detect-order.

Live mode reads Wecomm tenant schema:
  vendors, products, product_vendor (fallback: vendor_order_products),
  product_locations, product_barcodes.
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident


def _live_enabled() -> bool:
    flag = os.getenv("DETECT_ORDER_USE_LIVE_SQL", "1").lower()
    return flag in {"1", "true", "yes"}


class DetectOrderRepository:
    def __init__(self) -> None:
        self.configured = bool(os.getenv("DB_HOST"))
        self.live = self.configured and _live_enabled()
        self.schema = get_tenant_schema()
        self._db: WecommDatabaseConnector | None = None

    @property
    def mode(self) -> str:
        return "live" if self.live else "stub"

    def _conn(self) -> WecommDatabaseConnector:
        if self._db is None:
            self._db = WecommDatabaseConnector()
        return self._db

    def list_vendors(self) -> list[dict[str, Any]]:
        if not self.live:
            return [
                {"vendor_id": "V001", "vendor_name": "OM PRODUCE"},
                {"vendor_id": "V002", "vendor_name": "JALARAM"},
                {"vendor_id": "V003", "vendor_name": "DEEP FOODS"},
            ]
        sch = q_ident(self.schema)
        df = self._conn().read_sql(
            f"""
            SELECT id AS vendor_id, name AS vendor_name
            FROM {sch}.vendors
            WHERE deleted_at IS NULL
            ORDER BY name
            """
        )
        return [
            {"vendor_id": str(int(r.vendor_id)), "vendor_name": str(r.vendor_name)}
            for r in df.itertuples(index=False)
        ]

    def detect_vendor(
        self,
        *,
        vendor_id: str | None = None,
        vendor_name: str | None = None,
    ) -> dict[str, Any] | None:
        vendors = self.list_vendors()
        if vendor_id:
            for v in vendors:
                if str(v["vendor_id"]).upper() == str(vendor_id).strip().upper():
                    return v
        if vendor_name:
            needle = vendor_name.strip().upper()
            for v in vendors:
                name = str(v["vendor_name"]).upper()
                if needle in name or name in needle:
                    return v
        return None

    def fetch_vendor_items(self, vendor_id: str) -> list[dict[str, Any]]:
        """Step 1 — catalog items for vendor."""
        if not self.live:
            return self._stub_items(vendor_id)

        sch = q_ident(self.schema)
        vid = int(vendor_id)

        # Preferred: product_vendor link (has lead_time_days)
        df = self._conn().read_sql(
            f"""
            SELECT
              p.id AS item_id,
              p.sku,
              p.name AS description,
              pv.vendor_id,
              pv.lead_time_days,
              COALESCE(pv.price, p.purchase_price, p.price) AS vendor_price,
              COALESCE(p.min_reorder_quantity, 1) AS box_qty,
              (
                SELECT pb.barcode
                FROM {sch}.product_barcodes pb
                WHERE pb.product_id = p.id
                ORDER BY CASE WHEN pb.type = 'upc' THEN 0 ELSE 1 END, pb.id
                LIMIT 1
              ) AS upc
            FROM {sch}.product_vendor pv
            JOIN {sch}.products p ON p.id = pv.product_id
            WHERE pv.vendor_id = :vendor_id
              AND p.deleted_at IS NULL
              AND COALESCE(p.is_active, TRUE) = TRUE
            ORDER BY p.name
            """,
            {"vendor_id": vid},
        )

        source = "product_vendor"
        if df.empty:
            # Fallback while product_vendor is empty: items seen on that vendor's POs
            source = "vendor_order_products"
            df = self._conn().read_sql(
                f"""
                SELECT
                  p.id AS item_id,
                  p.sku,
                  p.name AS description,
                  vo.vendor_id,
                  NULL::integer AS lead_time_days,
                  COALESCE(p.purchase_price, p.price) AS vendor_price,
                  COALESCE(p.min_reorder_quantity, 1) AS box_qty,
                  (
                    SELECT pb.barcode
                    FROM {sch}.product_barcodes pb
                    WHERE pb.product_id = p.id
                    ORDER BY CASE WHEN pb.type = 'upc' THEN 0 ELSE 1 END, pb.id
                    LIMIT 1
                  ) AS upc
                FROM {sch}.vendor_order_products vop
                JOIN {sch}.vendor_orders vo ON vo.id = vop.vendor_order_id
                JOIN {sch}.products p ON p.id = vop.product_id
                WHERE vo.vendor_id = :vendor_id
                  AND vop.deleted_at IS NULL
                  AND p.deleted_at IS NULL
                GROUP BY p.id, p.sku, p.name, vo.vendor_id, p.purchase_price, p.price,
                         p.min_reorder_quantity
                ORDER BY p.name
                """,
                {"vendor_id": vid},
            )

        item_ids = [int(x) for x in df["item_id"].tolist()] if not df.empty else []
        expiry_map = self._fetch_expiration_days(item_ids)
        pallet_map = self._fetch_last_pallet_qty(item_ids, vid)

        items: list[dict[str, Any]] = []
        for r in df.itertuples(index=False):
            iid = str(int(r.item_id))
            items.append(
                {
                    "item_id": iid,
                    "upc": str(r.upc) if r.upc is not None else None,
                    "sku": str(r.sku) if r.sku is not None else None,
                    "description": str(r.description or ""),
                    "vendor_id": str(int(r.vendor_id)),
                    "demand_class": None,
                    "box_qty": max(int(r.box_qty or 1), 1),
                    "expiration_days_remaining": expiry_map.get(iid),
                    "last_pallet_qty": pallet_map.get(iid),
                    "lead_time_days": int(r.lead_time_days)
                    if r.lead_time_days is not None
                    else None,
                    "catalog_source": source,
                }
            )
        return items

    def _fetch_expiration_days(self, item_ids: list[int]) -> dict[str, float]:
        """Soonest batch expiration → days remaining (Step 5)."""
        if not item_ids:
            return {}
        sch = q_ident(self.schema)
        id_csv = ",".join(str(i) for i in item_ids)
        try:
            df = self._conn().read_sql(
                f"""
                SELECT
                  product_id,
                  MIN(expiration_date) AS soonest_exp
                FROM {sch}.product_batches
                WHERE product_id IN ({id_csv})
                  AND remaining_quantity > 0
                  AND expiration_date IS NOT NULL
                GROUP BY product_id
                """
            )
        except Exception:
            return {}
        out: dict[str, float] = {}
        today = pd.Timestamp.utcnow().normalize()
        for r in df.itertuples(index=False):
            exp = pd.to_datetime(r.soonest_exp, errors="coerce")
            if pd.isna(exp):
                continue
            days = (exp.normalize() - today).days
            out[str(int(r.product_id))] = float(max(days, 0))
        return out

    def _fetch_last_pallet_qty(self, item_ids: list[int], vendor_id: int) -> dict[str, float]:
        """Latest vendor_order_products.quantity for reference (Step 5)."""
        if not item_ids:
            return {}
        sch = q_ident(self.schema)
        id_csv = ",".join(str(i) for i in item_ids)
        try:
            df = self._conn().read_sql(
                f"""
                SELECT DISTINCT ON (vop.product_id)
                  vop.product_id,
                  vop.quantity
                FROM {sch}.vendor_order_products vop
                JOIN {sch}.vendor_orders vo ON vo.id = vop.vendor_order_id
                WHERE vo.vendor_id = :vendor_id
                  AND vop.product_id IN ({id_csv})
                  AND vop.deleted_at IS NULL
                ORDER BY vop.product_id, vop.created_at DESC NULLS LAST, vop.id DESC
                """,
                {"vendor_id": vendor_id},
            )
        except Exception:
            return {}
        return {
            str(int(r.product_id)): float(r.quantity)
            for r in df.itertuples(index=False)
            if r.quantity is not None
        }

    def fetch_available_stock(self, item_ids: list[str]) -> dict[str, float]:
        """Step 2 — sum on-hand qty from product_locations."""
        if not self.live:
            stock = {
                "I1001": 12.0,
                "I1002": 35.0,
                "I1003": 5.0,
                "I2001": 10.0,
                "I3001": 8.0,
            }
            return {i: float(stock.get(i, 0.0)) for i in item_ids}

        if not item_ids:
            return {}

        sch = q_ident(self.schema)
        ids = [int(x) for x in item_ids]
        id_csv = ",".join(str(i) for i in ids)
        df = self._conn().read_sql(
            f"""
            SELECT product_id, COALESCE(SUM(quantity), 0) AS qty
            FROM {sch}.product_locations
            WHERE deleted_at IS NULL
              AND product_id IN ({id_csv})
            GROUP BY product_id
            """
        )
        found = {str(int(r.product_id)): float(r.qty) for r in df.itertuples(index=False)}
        return {i: float(found.get(i, 0.0)) for i in item_ids}

    def _stub_items(self, vendor_id: str) -> list[dict[str, Any]]:
        catalog = {
            "V001": [
                {
                    "item_id": "I1001",
                    "upc": "0000000000042",
                    "sku": "OKRA-25",
                    "description": "CHINESE OKRA 25-30 LB",
                    "vendor_id": "V001",
                    "demand_class": "intermittent",
                    "box_qty": 25,
                    "expiration_days_remaining": 10,
                    "last_pallet_qty": 50,
                },
                {
                    "item_id": "I1002",
                    "upc": "0000000000043",
                    "sku": "GUVAR-20",
                    "description": "GUVAR BEANS 20 LB",
                    "vendor_id": "V001",
                    "demand_class": "smooth",
                    "box_qty": 20,
                    "expiration_days_remaining": 45,
                    "last_pallet_qty": 40,
                },
            ],
            "V002": [
                {
                    "item_id": "I2001",
                    "upc": "8901030865482",
                    "sku": "AMUL-BUTTER",
                    "description": "AMUL BUTTER 500G",
                    "vendor_id": "V002",
                    "demand_class": "smooth",
                    "box_qty": 12,
                    "expiration_days_remaining": 60,
                    "last_pallet_qty": 48,
                },
            ],
        }
        return list(catalog.get(str(vendor_id).upper(), []))
