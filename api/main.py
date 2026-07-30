"""
Inventory AI API — uploads, train jobs, and store UI.

Local:
  uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Cloud Run:
  uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from api.routes import detect_order, explain, system, train, uploads, vendor_order  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="Inventory AI",
    description=(
        "Store-facing API: W-1 detect-order, uploads, train, and explainability."
    ),
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ALLOW_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(uploads.router)
app.include_router(train.router)
app.include_router(system.router)
app.include_router(explain.router)
app.include_router(vendor_order.router)
app.include_router(detect_order.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def store_ui() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise RuntimeError("Store UI missing: api/static/index.html")
    return FileResponse(index)


def create_app() -> FastAPI:
    return app
