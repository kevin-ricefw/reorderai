"""Request / response models for the single vendor-order API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class VendorOrderRequest(BaseModel):
    """
    One-shot request your TL described:

      1) detect / identify vendor  (optional — omit to only list vendors)
      2) fetch items for that vendor from DB
      3) fetch available (on-hand) qty per item from DB
      4) qty_to_order = max(0, spread_stock - available_stock)
    """

    vendor_id: str | None = Field(
        default=None,
        description="Vendor ID from DB. If omitted with no vendor_name, returns vendor list only.",
    )
    vendor_name: str | None = Field(
        default=None,
        description="Vendor display name (fuzzy match allowed in stub).",
    )
    # Optional overrides when DB target/spread is missing
    default_spread_stock: float | None = Field(
        default=None,
        description="Fallback target/spread stock if DB has no per-item target.",
    )
    only_items_needing_order: bool = Field(
        default=True,
        description="If true, response.items only includes rows with qty_to_order > 0.",
    )


class VendorInfo(BaseModel):
    vendor_id: str
    vendor_name: str
    detected: bool = True
    source: str = "db"


class VendorOrderItem(BaseModel):
    item_id: str
    upc: str | None = None
    sku: str | None = None
    description: str
    vendor_id: str
    # From DB
    spread_stock: float = Field(description="Target / spread / par level from DB")
    available_stock: float = Field(description="Current on-hand / available qty from DB")
    # Calculated
    qty_to_order: float = Field(description="max(0, spread_stock - available_stock)")
    unit: str = "each"
    pack_size: int = 1
    extra: dict[str, Any] = Field(default_factory=dict)


class VendorOrderResponse(BaseModel):
    ok: bool = True
    vendors: list[VendorInfo] = Field(
        default_factory=list,
        description="All vendors from DB (always included so UI can show the list).",
    )
    vendor: VendorInfo | None = None
    item_count: int = 0
    order_line_count: int = 0
    total_units_to_order: float = 0.0
    items: list[VendorOrderItem] = Field(default_factory=list)
    db_mode: Literal["stub", "live"] = "stub"
    message: str = ""


class VendorDetectResponse(BaseModel):
    """List / detect vendors."""

    ok: bool = True
    db_mode: Literal["stub", "live"] = "stub"
    vendors: list[VendorInfo]
