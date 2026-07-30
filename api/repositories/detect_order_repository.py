"""
DB reads for W-1 detect-order (Steps 1–2 + Step 5 reference fields).

Uses Wecomm Azure Postgres when DETECT_ORDER_USE_LIVE_SQL=1 and queries are wired.
Until then, stub rows are returned even if DB_HOST is configured.
"""

from __future__ import annotations

import os
from typing import Any


class DetectOrderRepository:
    def __init__(self) -> None:
        self.configured = bool(os.getenv("DB_HOST"))
        # Flip on only after live SQL methods below are implemented for real tables
        self.live = os.getenv("DETECT_ORDER_USE_LIVE_SQL", "").lower() in {
            "1",
            "true",
            "yes",
        }

    @property
    def mode(self) -> str:
        if self.live:
            return "live"
        return "stub"

    def list_vendors(self) -> list[dict[str, Any]]:
        if self.live:
            raise NotImplementedError("Live list_vendors not wired.")
        return [
            {"vendor_id": "V001", "vendor_name": "OM PRODUCE"},
            {"vendor_id": "V002", "vendor_name": "JALARAM"},
            {"vendor_id": "V003", "vendor_name": "DEEP FOODS"},
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
        """
        Step 1 — items for vendor.

        Each row should eventually include from DB:
          item_id, upc, description, demand_class,
          box_qty, expiration_days_remaining, last_pallet_qty
        """
        if self.live:
            raise NotImplementedError("Live fetch_vendor_items not wired.")
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
                {
                    "item_id": "I1003",
                    "upc": "0000000000044",
                    "sku": "METHI-10",
                    "description": "FRESH METHI 10 LB",
                    "vendor_id": "V001",
                    "demand_class": "lumpy",
                    "box_qty": 10,
                    "expiration_days_remaining": 6,
                    "last_pallet_qty": 20,
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
            "V003": [
                {
                    "item_id": "I3001",
                    "upc": "011111222233",
                    "sku": "DEEP-SAMOSA",
                    "description": "DEEP SAMOSA 50CT",
                    "vendor_id": "V003",
                    "demand_class": "intermittent",
                    "box_qty": 1,
                    "expiration_days_remaining": 90,
                    "last_pallet_qty": 10,
                },
            ],
        }
        return list(catalog.get(str(vendor_id).upper(), []))

    def fetch_available_stock(self, item_ids: list[str]) -> dict[str, float]:
        """Step 2 — available / on-hand qty."""
        if self.live:
            raise NotImplementedError("Live fetch_available_stock not wired.")
        stock = {
            "I1001": 12.0,
            "I1002": 35.0,
            "I1003": 5.0,
            "I2001": 10.0,
            "I3001": 8.0,
        }
        return {i: float(stock.get(i, 0.0)) for i in item_ids}
