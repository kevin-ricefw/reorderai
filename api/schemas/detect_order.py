"""W-1 Detect Order — request / response schemas (design doc)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DetectOrderRequest(BaseModel):
    """
    Workflow W-1 inputs:
      - Vendor
      - Lead time (days until delivery)
      - Time to cover (extra days the next pallet should cover)
    """

    vendor_id: str | None = None
    vendor_name: str | None = None
    lead_time_days: int = Field(ge=0, description="Days from order to receipt")
    time_to_cover_days: int = Field(ge=0, description="Extra days of cover beyond lead time")
    include_zero_orders: bool = Field(
        default=False,
        description="If true, include items with qty_to_order == 0 in the response.",
    )
    generate_justification: bool = Field(
        default=True,
        description="Step 6 — attach plain-English GPT/template justification per line.",
    )


class VendorInfo(BaseModel):
    vendor_id: str
    vendor_name: str
    detected: bool = True


class DetectOrderItem(BaseModel):
    item_id: str
    upc: str | None = None
    sku: str | None = None
    description: str
    vendor_id: str

    # Demand class (from nightly classification)
    demand_class: str | None = None

    # Step 2 — stock from DB
    available_stock: float
    last_pallet_qty: float | None = None
    expiration_days_remaining: float | None = None
    box_qty: int = 1

    # Step 3 — forecast from forecast_store
    horizon_days: int
    forecast_horizon_used: int
    p50_demand: float
    p90_demand: float
    projected_stock_required: float = Field(
        description="P90 demand used for ordering (possibly expiry-capped)"
    )
    projected_stock_at_arrival: float = Field(
        description="Expected on-hand when order arrives (after lead-time demand)"
    )

    # Step 4–5 — order math + validation
    raw_qty_to_order: float
    qty_to_order: float
    qty_before_box_round: float
    expiry_capped: bool = False
    expiry_cap_days: float | None = None
    box_rounded: bool = False
    validation_notes: list[str] = Field(default_factory=list)

    # Step 6
    justification: str = ""

    extra: dict[str, Any] = Field(default_factory=dict)


class DetectOrderResponse(BaseModel):
    ok: bool = True
    run_id: str | None = None
    vendors: list[VendorInfo] = Field(default_factory=list)
    vendor: VendorInfo | None = None
    lead_time_days: int = 0
    time_to_cover_days: int = 0
    x_days: int = Field(0, description="Lead Time + Time to Cover")
    item_count: int = 0
    order_line_count: int = 0
    total_units_to_order: float = 0.0
    items: list[DetectOrderItem] = Field(default_factory=list)
    db_mode: Literal["stub", "live"] = "stub"
    forecast_mode: Literal["stub", "live", "batch"] = "stub"
    message: str = ""


class OrderRunSummary(BaseModel):
    run_id: str
    vendor_id: str
    vendor_name: str
    created_at: str
    x_days: int
    order_line_count: int
