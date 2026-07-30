"""Persist detect-order runs for chatbot traceability (Decision 8)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.data_paths import PROJECT_ROOT

RUNS_DIR = PROJECT_ROOT / "data" / "cache" / "order_runs"


def _ensure_dir() -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return RUNS_DIR


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{ts}_{uuid.uuid4().hex[:8]}"


def save_order_run(payload: dict[str, Any]) -> str:
    """Write full detect-order response JSON; return run_id."""
    run_id = payload.get("run_id") or new_run_id()
    payload = {**payload, "run_id": run_id}
    path = _ensure_dir() / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    # lightweight index
    index_path = RUNS_DIR / "index.jsonl"
    with index_path.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "run_id": run_id,
                    "vendor_id": (payload.get("vendor") or {}).get("vendor_id"),
                    "vendor_name": (payload.get("vendor") or {}).get("vendor_name"),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "order_line_count": payload.get("order_line_count"),
                    "x_days": payload.get("x_days"),
                }
            )
            + "\n"
        )
    return run_id


def load_order_run(run_id: str) -> dict[str, Any] | None:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
