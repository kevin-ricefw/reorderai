"""Explainability endpoint for Window 1 reorder chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.services.explain_service import build_explain_context, explain_reorder

router = APIRouter(prefix="/api", tags=["explain"])


class ExplainRequest(BaseModel):
    question: str = Field(..., min_length=1)
    product: dict[str, Any] = Field(default_factory=dict)
    vendor: str = ""


@router.post("/explain")
async def explain(req: ExplainRequest) -> dict:
    ctx = build_explain_context(req.product, vendor=req.vendor)
    answer = explain_reorder(req.question, ctx)
    return {"answer": answer, "context": ctx}
