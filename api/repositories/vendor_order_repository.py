"""
DB access for vendor-order API.

Replace stub implementations with real SQL once TL provides the new DB credentials.
All methods return plain dicts / lists — no FastAPI types here.
"""

from __future__ import annotations

import os
from typing import Any


class VendorOrderRepository:
    """
    Contract:

      detect_vendor(...)           -> vendor row or None
      list_vendors()               -> vendor rows
      fetch_vendor_items(...)      -> items for vendor (ids, upc, description, spread_stock)
      fetch_available_quantities() -> map item_id -> available_stock
    """

    def __init__(self) -> None:
        self.configured = bool(os.getenv("DB_HOST"))
        self.live = os.getenv("DETECT_ORDER_USE_LIVE_SQL", "").lower() in {
            "1",
            "true",
            "yes",
        }

    @property
    def mode(self) -> str:
        return "live" if self.live else "stub"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_vendor(
        self,
        *,
        vendor_id: str | None = None,
        vendor_name: str | None = None,
    ) -> dict[str, Any] | None:
        if self.live:
            return self._live_detect_vendor(vendor_id=vendor_id, vendor_name=vendor_name)
        return self._stub_detect_vendor(vendor_id=vendor_id, vendor_name=vendor_name)

    def list_vendors(self) -> list[dict[str, Any]]:
        if self.live:
            return self._live_list_vendors()
        return self._stub_list_vendors()

    def fetch_vendor_items(self, vendor_id: str) -> list[dict[str, Any]]:
        """Items sold/supplied by this vendor, including spread/target stock from DB."""
        if self.live:
            return self._live_fetch_vendor_items(vendor_id)
        return self._stub_fetch_vendor_items(vendor_id)

    def fetch_available_quantities(self, item_ids: list[str]) -> dict[str, float]:
        """Current available / on-hand qty keyed by item_id."""
        if self.live:
            return self._live_fetch_available_quantities(item_ids)
        return self._stub_fetch_available_quantities(item_ids)

    # ------------------------------------------------------------------
    # STUB data (works before DB access arrives)
    # ------------------------------------------------------------------

    def _stub_list_vendors(self) -> list[dict[str, Any]]:
        return [
            {"vendor_id": "V001", "vendor_name": "OM PRODUCE"},
            {"vendor_id": "V002", "vendor_name": "JALARAM"},
            {"vendor_id": "V003", "vendor_name": "DEEP FOODS"},
        ]

    def _stub_detect_vendor(
        self,
        *,
        vendor_id: str | None,
        vendor_name: str | None,
    ) -> dict[str, Any] | None:
        vendors = self._stub_list_vendors()
        if vendor_id:
            for v in vendors:
                if str(v["vendor_id"]).upper() == str(vendor_id).strip().upper():
                    return v
        if vendor_name:
            needle = vendor_name.strip().upper()
            for v in vendors:
                if needle in str(v["vendor_name"]).upper() or str(v["vendor_name"]).upper() in needle:
                    return v
        return None

    def _stub_fetch_vendor_items(self, vendor_id: str) -> list[dict[str, Any]]:
        catalog = {
            "V001": [
                {
                    "item_id": "I1001",
                    "upc": "0000000000042",
                    "sku": "OKRA-25",
                    "description": "CHINESE OKRA 25-30 LB",
                    "vendor_id": "V001",
                    "spread_stock": 40.0,
                    "pack_size": 1,
                    "unit": "lb",
                },
                {
                    "item_id": "I1002",
                    "upc": "0000000000043",
                    "sku": "GUVAR-20",
                    "description": "GUVAR BEANS 20 LB",
                    "vendor_id": "V001",
                    "spread_stock": 30.0,
                    "pack_size": 1,
                    "unit": "lb",
                },
                {
                    "item_id": "I1003",
                    "upc": "0000000000044",
                    "sku": "METHI-10",
                    "description": "FRESH METHI 10 LB",
                    "vendor_id": "V001",
                    "spread_stock": 20.0,
                    "pack_size": 1,
                    "unit": "lb",
                },
            ],
            "V002": [
                {
                    "item_id": "I2001",
                    "upc": "8901030865482",
                    "sku": "AMUL-BUTTER",
                    "description": "AMUL BUTTER 500G",
                    "vendor_id": "V002",
                    "spread_stock": 48.0,
                    "pack_size": 12,
                    "unit": "each",
                },
            ],
            "V003": [
                {
                    "item_id": "I3001",
                    "upc": "011111222233",
                    "sku": "DEEP-SAMOSA",
                    "description": "DEEP SAMOSA 50CT",
                    "vendor_id": "V003",
                    "spread_stock": 60.0,
                    "pack_size": 1,
                    "unit": "case",
                },
            ],
        }
        return list(catalog.get(str(vendor_id).upper(), []))

    def _stub_fetch_available_quantities(self, item_ids: list[str]) -> dict[str, float]:
        # Pretend inventory snapshot from DB
        stock = {
            "I1001": 12.0,  # need 40-12=28
            "I1002": 30.0,  # need 30-30=0
            "I1003": 5.0,  # need 20-5=15
            "I2001": 10.0,
            "I3001": 55.0,
        }
        return {i: float(stock.get(i, 0.0)) for i in item_ids}

    # ------------------------------------------------------------------
    # LIVE placeholders — fill in when new DB access arrives
    # ------------------------------------------------------------------

    def _live_detect_vendor(
        self,
        *,
        vendor_id: str | None,
        vendor_name: str | None,
    ) -> dict[str, Any] | None:
        # TODO: SELECT vendor_id, vendor_name FROM vendors WHERE ...
        raise NotImplementedError(
            "NEW_ORDER_DB is configured but live SQL is not wired yet. "
            "Give table/column names and this method will be filled in."
        )

    def _live_list_vendors(self) -> list[dict[str, Any]]:
        # TODO: SELECT vendor_id, vendor_name FROM vendors
        raise NotImplementedError("Live list_vendors SQL not wired yet.")

    def _live_fetch_vendor_items(self, vendor_id: str) -> list[dict[str, Any]]:
        # TODO: SELECT item_id, upc, description, spread_stock / par / target
        #       FROM vendor_items WHERE vendor_id = :vendor_id
        raise NotImplementedError("Live fetch_vendor_items SQL not wired yet.")

    def _live_fetch_available_quantities(self, item_ids: list[str]) -> dict[str, float]:
        # TODO: SELECT item_id, available_qty FROM inventory WHERE item_id IN (...)
        raise NotImplementedError("Live fetch_available_quantities SQL not wired yet.")
