"""Phase-3 investigate chatbot schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatAskRequest(BaseModel):
    run_id: str = Field(..., description="Saved detect-order run_id (Decision 8)")
    question: str = Field(..., description="Natural-language question about that run")


class ChatToolRequest(BaseModel):
    run_id: str
    tool: str
    item_id: str | None = None
    item_id_a: str | None = None
    item_id_b: str | None = None
    only_nonzero: bool = True


class ChatResponse(BaseModel):
    ok: bool = True
    run_id: str
    tool: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
