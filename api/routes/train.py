"""Train / retrain job endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.services import train_service

router = APIRouter(prefix="/api", tags=["train"])


class TrainRequest(BaseModel):
    start_date: str | None = Field(
        default=None,
        description="Optional YYYY-MM-DD. Defaults to earliest uploaded sales day.",
    )
    end_date: str | None = Field(
        default=None,
        description="Optional YYYY-MM-DD. Defaults to latest uploaded sales day.",
    )


def _parse_date(value: str | None, field: str) -> date | None:
    if value is None or not str(value).strip():
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be YYYY-MM-DD") from exc


@router.post("/train")
async def start_train(body: TrainRequest | None = None) -> dict:
    """Start background retrain on current sales + inventory. Returns job_id immediately."""
    body = body or TrainRequest()
    try:
        job = train_service.start_training_job(
            start_date=_parse_date(body.start_date, "start_date"),
            end_date=_parse_date(body.end_date, "end_date"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job


@router.get("/train/status")
async def train_status_latest() -> dict:
    job = train_service.latest_job()
    if not job:
        return {"status": "idle", "job": None, "is_training": False}
    return {
        "status": job.get("status"),
        "is_training": train_service.is_training(),
        "job": job,
    }


@router.get("/train/{job_id}")
async def train_status(job_id: str) -> dict:
    job = train_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job
