"""Background training jobs for retrain-on-upload."""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from api.services.upload_service import detect_sales_date_range
from config.data_paths import PROJECT_ROOT

JOBS_DIR = PROJECT_ROOT / "outputs" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_current_job_id: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _write_job(job: dict[str, Any]) -> None:
    path = _job_path(job["job_id"])
    path.write_text(json.dumps(job, indent=2), encoding="utf-8")


def _read_job(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def get_job(job_id: str) -> dict[str, Any] | None:
    return _read_job(job_id)


def latest_job() -> dict[str, Any] | None:
    files = sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))


def is_training() -> bool:
    with _lock:
        if _current_job_id is None:
            return False
        job = _read_job(_current_job_id)
        return bool(job and job.get("status") == "running")


def start_training_job(
    *,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """
    Kick off full SKU analysis in a background thread.

    Returns immediately with job metadata. Poll GET /api/train/{job_id}.
    """
    global _current_job_id

    if is_training():
        raise RuntimeError("A training job is already running. Wait for it to finish.")

    detected = detect_sales_date_range()
    if start_date is None or end_date is None:
        if not detected:
            raise ValueError(
                "No sales files found. Upload at least one Product Sales CSV before training."
            )
        start_date = start_date or detected[0]
        end_date = end_date or detected[1]

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date.")

    job_id = uuid.uuid4().hex[:12]
    job: dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": _utc_now(),
        "started_at": None,
        "finished_at": None,
        "analysis_start": start_date.isoformat(),
        "analysis_end": end_date.isoformat(),
        "message": "Queued",
        "error": None,
        "summary": None,
    }
    _write_job(job)

    with _lock:
        _current_job_id = job_id

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, start_date, end_date),
        name=f"train-{job_id}",
        daemon=True,
    )
    thread.start()
    return job


def _run_job(job_id: str, start_date: date, end_date: date) -> None:
    global _current_job_id
    job = _read_job(job_id) or {"job_id": job_id}
    job["status"] = "running"
    job["started_at"] = _utc_now()
    job["message"] = "Training models and rebuilding reorder recommendations…"
    _write_job(job)

    try:
        from scripts.run_sku_analysis import run_full_analysis

        summary = run_full_analysis(start_date=start_date, end_date=end_date)
        job["status"] = "completed"
        job["finished_at"] = _utc_now()
        job["message"] = "Training completed successfully."
        job["summary"] = summary
        job["error"] = None
        _write_job(job)
    except Exception as exc:
        job["status"] = "failed"
        job["finished_at"] = _utc_now()
        job["message"] = "Training failed."
        job["error"] = f"{exc}\n{traceback.format_exc()}"
        _write_job(job)
    finally:
        with _lock:
            if _current_job_id == job_id:
                _current_job_id = None
