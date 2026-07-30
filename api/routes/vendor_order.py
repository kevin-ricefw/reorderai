"""
Vendor Order API — single pipeline your TL asked for.

Main endpoint:
  POST /api/vendor-order
      1) detect vendor
      2) fetch items from DB
      3) fetch available qty for items from DB
      4) qty_to_order = max(0, spread_stock - available_stock)

Helper (optional):
  GET  /api/vendor-order/vendors?q=OM
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from api.schemas.vendor_order import VendorDetectResponse, VendorOrderRequest, VendorOrderResponse
from api.services import vendor_order_service

router = APIRouter(prefix="/api/vendor-order", tags=["vendor-order"])


@router.get(
    "",
    response_model=VendorOrderResponse,
    summary="List vendors (and optionally calculate if query params set)",
)
async def vendor_order_get(
    vendor_id: str | None = Query(default=None),
    vendor_name: str | None = Query(default=None),
    only_items_needing_order: bool = Query(default=True),
) -> VendorOrderResponse:
    """GET with no params → vendors list. With vendor_id/name → full order calc."""
    return vendor_order_service.calculate_vendor_order(
        VendorOrderRequest(
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            only_items_needing_order=only_items_needing_order,
        )
    )


@router.post(
    "",
    response_model=VendorOrderResponse,
    summary="Vendor order calculate (ONE API)",
    description=(
        "Always includes `vendors` list. "
        "Pass vendor_id or vendor_name to also fetch items + available stock + qty_to_order. "
        "Empty body → vendors only."
    ),
)
async def vendor_order_calculate(body: VendorOrderRequest) -> VendorOrderResponse:
    return vendor_order_service.calculate_vendor_order(body)


@router.get(
    "/vendors",
    response_model=VendorDetectResponse,
    summary="Detect / list vendors",
)
async def vendor_order_detect(
    q: str | None = Query(default=None, description="Optional vendor name/id filter"),
) -> VendorDetectResponse:
    return vendor_order_service.detect_vendors(q)
