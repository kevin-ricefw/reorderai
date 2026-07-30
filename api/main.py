"""
Reorder AI API — TL integration endpoints (Phases 1–3).

  uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from api.routes import chatbot, detect_order, system  # noqa: E402

app = FastAPI(
    title="Reorder AI",
    description=(
        "Wecomm W-1 detect-order + Phase-1 forecast_store + Phase-3 investigate chatbot. "
        "TL UI owns the storefront; this service exposes APIs only."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(detect_order.router)
app.include_router(chatbot.router)


@app.get("/")
async def root() -> dict:
    return {
        "service": "reorder-ai",
        "version": "3.0.0",
        "docs": "/docs",
        "endpoints": {
            "health": "GET /api/health",
            "db_health": "GET /api/db-health",
            "list_vendors": "GET /api/detect-order",
            "detect_order": "POST /api/detect-order",
            "order_run": "GET /api/detect-order/runs/{run_id}",
            "chatbot_tools": "GET /api/chatbot/tools",
            "chatbot_ask": "POST /api/chatbot/ask",
            "chatbot_tool": "POST /api/chatbot/tool",
            "nightly_batch": "python scripts/run_nightly_forecast.py",
        },
    }


def create_app() -> FastAPI:
    return app
