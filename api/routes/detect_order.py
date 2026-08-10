"""
W-1 Detect Order API (design doc §5.3).

POST /api/detect-order
  I/P: vendor, lead_time_days, time_to_cover_days, uplift_types, risk_factor
  O/P: items to order, stock + projected stock, justification, run_id

GET  /api/detect-order            → vendor list (pass no vendor)
GET  /api/detect-order/runs/{id}  → saved run for chatbot (Decision 8)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from api.schemas.detect_order import DetectOrderRequest, DetectOrderResponse, UpliftType
from api.services import detect_order_service
from api.services.order_export import order_run_to_excel_bytes

router = APIRouter(prefix="/api/detect-order", tags=["detect-order"])


@router.get(
    "",
    response_model=DetectOrderResponse,
    summary="List vendors or run detect-order via query params",
)
def detect_order_get(
    tenant_id: str | None = Query(
        default=None, description="Tenant schema override (wecomm_<uuid>); omit for server default."
    ),
    vendor_id: str | None = Query(default=None),
    vendor_name: str | None = Query(default=None),
    lead_time_days: int = Query(default=5, ge=0),
    time_to_cover_days: int = Query(default=7, ge=0),
    uplift_types: list[UpliftType] | None = Query(
        default=None,
        description="Multi-select: weekend, festival, trend. Omit = weekend+festival. Empty = none.",
    ),
    risk_factor: int = Query(default=50, ge=0, le=100),
    generate_justification: bool = Query(
        default=True,
        description="Ignored — justification always uses the report-style template (no GPT).",
    ),
) -> DetectOrderResponse:
    # sync def → FastAPI runs in threadpool (won't block other requests)
    return detect_order_service.detect_order(
        DetectOrderRequest(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            lead_time_days=lead_time_days,
            time_to_cover_days=time_to_cover_days,
            uplift_types=uplift_types if uplift_types is not None else ["weekend", "festival"],
            risk_factor=risk_factor,
            generate_justification=generate_justification,
        )
    )


@router.post(
    "",
    response_model=DetectOrderResponse,
    summary="Detect order for vendor (W-1)",
    description=(
        "Inputs: vendor + L + C + optional uplift_types (multi-select) + risk_factor (0–100). "
        "Returns ORDER/WATCH lines sized for cover C after lead L. Saves run_id for chatbot."
    ),
)
def detect_order_post(body: DetectOrderRequest) -> DetectOrderResponse:
    return detect_order_service.detect_order(body)


@router.get(
    "/runs/{run_id}",
    summary="Load a saved order run (chatbot scope)",
)
def get_detect_order_run(run_id: str) -> dict:
    data = detect_order_service.get_order_run(run_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Order run not found: {run_id}")
    return data


@router.get(
    "/runs/{run_id}/export.xlsx",
    summary="Download vendor order sheet as Excel",
)
def export_detect_order_run(run_id: str) -> Response:
    data = detect_order_service.get_order_run(run_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"Order run not found: {run_id}")
    payload = order_run_to_excel_bytes(data)
    vendor = ((data.get("vendor") or {}).get("vendor_name") or "vendor").replace(" ", "_")
    filename = f"order_{vendor}_{run_id}.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
