"""
Single vendor-order pipeline (TL requirement):

  1. Order detect for vendor (always returns full vendors list)
  2. Fetch items from DB
  3. Fetch vendor/available quantity for those items from DB
  4. Calculate qty needed = spread_stock - available_stock
"""

from __future__ import annotations

from api.repositories.vendor_order_repository import VendorOrderRepository
from api.schemas.vendor_order import (
    VendorDetectResponse,
    VendorInfo,
    VendorOrderItem,
    VendorOrderRequest,
    VendorOrderResponse,
)


def _repo() -> VendorOrderRepository:
    return VendorOrderRepository()


def _vendor_infos(repo: VendorOrderRepository) -> list[VendorInfo]:
    return [
        VendorInfo(
            vendor_id=str(v["vendor_id"]),
            vendor_name=str(v["vendor_name"]),
            detected=True,
            source="db",
        )
        for v in repo.list_vendors()
    ]


def detect_vendors(query: str | None = None) -> VendorDetectResponse:
    repo = _repo()
    vendors = _vendor_infos(repo)
    if query:
        q = query.strip().upper()
        vendors = [
            v
            for v in vendors
            if q in v.vendor_id.upper() or q in v.vendor_name.upper()
        ]
    return VendorDetectResponse(
        ok=True,
        db_mode=repo.mode,  # type: ignore[arg-type]
        vendors=vendors,
    )


def calculate_vendor_order(req: VendorOrderRequest) -> VendorOrderResponse:
    """
    THE one API body of work:

      list vendors → detect vendor → fetch items → fetch available qty → spread - available
    """
    repo = _repo()
    all_vendors = _vendor_infos(repo)

    # No vendor selected → return vendors list only (order-detect step)
    if not (req.vendor_id or req.vendor_name):
        return VendorOrderResponse(
            ok=True,
            vendors=all_vendors,
            vendor=None,
            item_count=0,
            order_line_count=0,
            total_units_to_order=0.0,
            items=[],
            db_mode=repo.mode,  # type: ignore[arg-type]
            message=f"{len(all_vendors)} vendors available. Pass vendor_id or vendor_name to calculate order.",
        )

    # 1) Order detect for vendor
    vendor = repo.detect_vendor(vendor_id=req.vendor_id, vendor_name=req.vendor_name)
    if vendor is None:
        return VendorOrderResponse(
            ok=False,
            vendors=all_vendors,
            vendor=VendorInfo(
                vendor_id=req.vendor_id or "",
                vendor_name=req.vendor_name or "",
                detected=False,
                source="db",
            ),
            item_count=0,
            order_line_count=0,
            total_units_to_order=0.0,
            items=[],
            db_mode=repo.mode,  # type: ignore[arg-type]
            message="Vendor not found. See vendors list in response.",
        )

    vendor_id = str(vendor["vendor_id"])
    vendor_name = str(vendor["vendor_name"])

    # 2) Fetch items from DB (includes spread / target stock)
    raw_items = repo.fetch_vendor_items(vendor_id)
    if req.default_spread_stock is not None:
        for it in raw_items:
            if it.get("spread_stock") is None:
                it["spread_stock"] = float(req.default_spread_stock)

    item_ids = [str(it["item_id"]) for it in raw_items]

    # 3) Fetch available quantity for those items from DB
    available_map = repo.fetch_available_quantities(item_ids)

    # 4) Calculate items needed = spread_stock - available_stock
    lines: list[VendorOrderItem] = []
    for it in raw_items:
        item_id = str(it["item_id"])
        spread = float(it.get("spread_stock") or 0.0)
        available = float(available_map.get(item_id, 0.0))
        needed = max(0.0, spread - available)
        lines.append(
            VendorOrderItem(
                item_id=item_id,
                upc=it.get("upc"),
                sku=it.get("sku"),
                description=str(it.get("description") or ""),
                vendor_id=vendor_id,
                spread_stock=spread,
                available_stock=available,
                qty_to_order=needed,
                unit=str(it.get("unit") or "each"),
                pack_size=max(int(it.get("pack_size") or 1), 1),
                extra={
                    k: v
                    for k, v in it.items()
                    if k
                    not in {
                        "item_id",
                        "upc",
                        "sku",
                        "description",
                        "vendor_id",
                        "spread_stock",
                        "pack_size",
                        "unit",
                    }
                },
            )
        )

    order_lines = [x for x in lines if x.qty_to_order > 0]
    out_items = order_lines if req.only_items_needing_order else lines

    return VendorOrderResponse(
        ok=True,
        vendors=all_vendors,
        vendor=VendorInfo(
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            detected=True,
            source="db",
        ),
        item_count=len(lines),
        order_line_count=len(order_lines),
        total_units_to_order=round(sum(x.qty_to_order for x in order_lines), 2),
        items=out_items,
        db_mode=repo.mode,  # type: ignore[arg-type]
        message=(
            f"Detected {vendor_name}. "
            f"{len(lines)} items from DB, {len(order_lines)} need order "
            f"(spread_stock - available_stock). "
            f"{len(all_vendors)} vendors listed."
        ),
    )
