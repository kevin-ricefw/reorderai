"""Project paths."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
ORDER_RUNS_DIR = CACHE_DIR / "order_runs"
