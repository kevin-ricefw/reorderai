"""
Phase 3 — Investigate chatbot API.

Fixed tools only (Decision 7). Scoped to order run_id (Decision 8).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.schemas.chatbot import ChatAskRequest, ChatResponse, ChatToolRequest
from api.services import chatbot_tools

router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])


@router.get("/tools")
def chatbot_tools_list() -> dict:
    return {"tools": chatbot_tools.list_tools()}


@router.post("/ask", response_model=ChatResponse)
def chatbot_ask(body: ChatAskRequest) -> ChatResponse:
    try:
        routed = chatbot_tools.route_question(body.run_id, body.question)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ChatResponse(
        ok=True,
        run_id=body.run_id,
        tool=str(routed.get("tool")),
        result=dict(routed.get("result") or {}),
        message="Answer grounded in approved tool output for this run_id.",
    )


@router.post("/tool", response_model=ChatResponse)
def chatbot_tool(body: ChatToolRequest) -> ChatResponse:
    kwargs: dict = {"run_id": body.run_id}
    if body.tool in {"get_item_details", "why_item"}:
        if not body.item_id:
            raise HTTPException(status_code=400, detail="item_id required")
        kwargs["item_id"] = body.item_id
    if body.tool == "compare_items":
        if not body.item_id_a or not body.item_id_b:
            raise HTTPException(status_code=400, detail="item_id_a and item_id_b required")
        kwargs["item_id_a"] = body.item_id_a
        kwargs["item_id_b"] = body.item_id_b
    if body.tool == "list_order_lines":
        kwargs["only_nonzero"] = body.only_nonzero
    try:
        result = chatbot_tools.call_tool(body.tool, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ChatResponse(
        ok=True,
        run_id=body.run_id,
        tool=body.tool,
        result=result,
        message="Tool executed.",
    )
