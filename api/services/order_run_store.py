"""Persist detect-order runs for chatbot / audit (run_id)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.data_paths import ORDER_RUNS_DIR


def _ensure_dir() -> Path:
    ORDER_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return ORDER_RUNS_DIR


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{ts}_{uuid.uuid4().hex[:8]}"


def save_order_run(payload: dict[str, Any]) -> str:
    run_id = payload.get("run_id") or new_run_id()
    payload = {**payload, "run_id": run_id}
    path = _ensure_dir() / f"{run_id}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    index = _ensure_dir() / "index.jsonl"
    with index.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": run_id, "saved_at": datetime.now(timezone.utc).isoformat()}) + "\n")
    return run_id


def load_order_run(run_id: str) -> dict[str, Any] | None:
    path = ORDER_RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
