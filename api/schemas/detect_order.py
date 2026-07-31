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
        default=False,
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

    demand_class: str | None = None
    forecast_source: str | None = None

    # Stock
    available_stock: float
    last_pallet_qty: float | None = None
    expiration_days_remaining: float | None = None
    box_qty: int = 1

    # Demand drivers
    ads: float = Field(0.0, description="Average daily sales")
    demand_std: float = Field(0.0, description="Daily demand std used for safety stock")
    safety_stock: float = Field(0.0, description="Safety stock for lead time")
    reorder_point: float = Field(0.0, description="AI minimum / ROP = ADS×L + SS")
    below_reorder_point: bool = False

    # Demand split: lead burn + post-arrival cover → total X = L+C
    lead_demand_ads: float = Field(0.0, description="ADS × L (sells before truck arrives)")
    lead_demand_p50: float = Field(0.0, description="ML P50 over lead days")
    cover_demand_ads: float = Field(0.0, description="ADS × C (after arrival)")
    cover_demand_p90: float = Field(0.0, description="ML P90 over cover days")
    ads_cover_qty: float = Field(0.0, description="Classic ADS × X + SS (X=L+C)")
    uplift_multiplier: float = 1.0
    uplift_rule: str | None = None
    p50_demand: float = Field(0.0, description="ML P50 for full window X=L+C")
    p90_demand: float = Field(0.0, description="ML P90 for full window X=L+C")
    ai_target_qty: float = Field(
        0.0, description="Order-up-to = max(P90_X uplifted, ADS×X + SS)"
    )

    horizon_days: int
    forecast_horizon_used: int
    projected_stock_required: float = Field(
        description="Target on-hand for the planning window (ai_target)"
    )
    projected_stock_at_arrival: float = Field(
        description="Expected on-hand when order arrives (after lead-time demand)"
    )

    # Order qty
    raw_qty_to_order: float
    qty_before_box_round: float
    qty_to_order: float
    cases_to_order: float = 0.0
    expiry_capped: bool = False
    expiry_cap_days: float | None = None
    box_rounded: bool = False
    validation_notes: list[str] = Field(default_factory=list)

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
    catalog_item_count: int = 0
    item_count: int = 0
    order_line_count: int = 0
    total_units_to_order: float = 0.0
    total_cases_to_order: float = 0.0
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
