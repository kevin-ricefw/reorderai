"""Upload sales and inventory CSV endpoints."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from api.services import upload_service

router = APIRouter(prefix="/api", tags=["uploads"])


def _parse_optional_date(value: str | None) -> date | None:
    if not value or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="sale_date must be YYYY-MM-DD",
        ) from exc


@router.post("/upload/sales")
async def upload_sales(
    file: UploadFile = File(..., description="POS Product Sales CSV for one day"),
    sale_date: str | None = Form(
        default=None,
        description="Optional YYYY-MM-DD if filename is not 'Product Sales MONTH DAY.csv'",
    ),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv sales file.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        result = upload_service.save_sales_upload(
            content,
            original_filename=file.filename,
            sale_date=_parse_optional_date(sale_date),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.post("/upload/inventory")
async def upload_inventory(
    file: UploadFile = File(..., description="Current inventory count CSV"),
) -> dict:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Upload a .csv inventory file.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    try:
        result = upload_service.save_inventory_upload(
            content, original_filename=file.filename
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/data/status")
async def data_status() -> dict:
    sales = upload_service.list_sales_files()
    dates = [s["sale_date"] for s in sales if s.get("sale_date")]
    window = upload_service.detect_sales_date_range()
    return {
        "sales_file_count": len(sales),
        "sales_date_min": window[0].isoformat() if window else None,
        "sales_date_max": window[1].isoformat() if window else None,
        "sales_files": sales[-14:],  # recent slice for UI
        "inventory": upload_service.inventory_status(),
        "server_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "has_dated_sales": bool(dates),
    }
