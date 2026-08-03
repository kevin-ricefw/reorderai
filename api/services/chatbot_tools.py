"""
Phase 3 — fixed read-only chatbot tools (Decision 7).

The chatbot may ONLY call these functions. No free-form SQL.
All answers are scoped to a saved detect-order run_id (Decision 8).
"""

from __future__ import annotations

from typing import Any, Callable

from api.services.order_run_store import load_order_run

ToolFn = Callable[..., dict[str, Any]]


def _require_run(run_id: str) -> dict[str, Any]:
    data = load_order_run(run_id)
    if not data:
        raise ValueError(f"Order run not found: {run_id}")
    return data


def tool_get_order_run(run_id: str) -> dict[str, Any]:
    """Full saved run summary (no secrets)."""
    data = _require_run(run_id)
    return {
        "run_id": data.get("run_id"),
        "vendor": data.get("vendor"),
        "lead_time_days": data.get("lead_time_days"),
        "time_to_cover_days": data.get("time_to_cover_days"),
        "x_days": data.get("x_days"),
        "order_line_count": data.get("order_line_count"),
        "total_units_to_order": data.get("total_units_to_order"),
        "message": data.get("message"),
        "db_mode": data.get("db_mode"),
        "forecast_mode": data.get("forecast_mode"),
    }


def tool_list_order_lines(run_id: str, *, only_nonzero: bool = True) -> dict[str, Any]:
    data = _require_run(run_id)
    items = list(data.get("items") or [])
    if only_nonzero:
        items = [i for i in items if float(i.get("qty_to_order") or 0) > 0]
    slim = [
        {
            "item_id": i.get("item_id"),
            "description": i.get("description"),
            "qty_to_order": i.get("qty_to_order"),
            "cases_to_order": i.get("cases_to_order"),
            "available_stock": i.get("available_stock"),
            "ads": i.get("ads"),
            "safety_stock": i.get("safety_stock"),
            "safety_stock_cover": i.get("safety_stock_cover"),
            "reorder_point": i.get("reorder_point"),
            "min_on_hand": i.get("min_on_hand"),
            "min_on_hand_source": i.get("min_on_hand_source"),
            "desired_stock": i.get("desired_stock"),
            "ai_target_qty": i.get("ai_target_qty"),
            "p90_demand": i.get("p90_demand"),
            "uplift_multiplier": i.get("uplift_multiplier"),
            "demand_class": i.get("demand_class"),
        }
        for i in items
    ]
    return {"run_id": run_id, "count": len(slim), "items": slim}


def tool_get_item_details(run_id: str, item_id: str) -> dict[str, Any]:
    data = _require_run(run_id)
    needle = str(item_id).strip()
    for i in data.get("items") or []:
        if str(i.get("item_id")) == needle:
            return {"run_id": run_id, "item": i}
    raise ValueError(f"Item {item_id} not in run {run_id}")


def tool_compare_items(run_id: str, item_id_a: str, item_id_b: str) -> dict[str, Any]:
    a = tool_get_item_details(run_id, item_id_a)["item"]
    b = tool_get_item_details(run_id, item_id_b)["item"]
    keys = [
        "description",
        "qty_to_order",
        "available_stock",
        "p50_demand",
        "p90_demand",
        "demand_class",
        "expiry_capped",
        "box_qty",
    ]
    return {
        "run_id": run_id,
        "item_a": {k: a.get(k) for k in keys},
        "item_b": {k: b.get(k) for k in keys},
    }


def tool_list_expiry_capped(run_id: str) -> dict[str, Any]:
    data = _require_run(run_id)
    capped = [
        {
            "item_id": i.get("item_id"),
            "description": i.get("description"),
            "expiration_days_remaining": i.get("expiration_days_remaining"),
            "qty_to_order": i.get("qty_to_order"),
            "expiry_cap_days": i.get("expiry_cap_days"),
        }
        for i in (data.get("items") or [])
        if i.get("expiry_capped")
    ]
    return {"run_id": run_id, "count": len(capped), "items": capped}


def tool_why_item(run_id: str, item_id: str) -> dict[str, Any]:
    item = tool_get_item_details(run_id, item_id)["item"]
    return {
        "run_id": run_id,
        "item_id": item_id,
        "justification": item.get("justification") or "",
        "qty_to_order": item.get("qty_to_order"),
        "p90_demand": item.get("p90_demand"),
        "available_stock": item.get("available_stock"),
    }


TOOL_REGISTRY: dict[str, ToolFn] = {
    "get_order_run": tool_get_order_run,
    "list_order_lines": tool_list_order_lines,
    "get_item_details": tool_get_item_details,
    "compare_items": tool_compare_items,
    "list_expiry_capped": tool_list_expiry_capped,
    "why_item": tool_why_item,
}


def list_tools() -> list[dict[str, str]]:
    return [
        {"name": "get_order_run", "args": "run_id", "desc": "Summary of a saved order run"},
        {
            "name": "list_order_lines",
            "args": "run_id, only_nonzero?=true",
            "desc": "List recommended order lines",
        },
        {
            "name": "get_item_details",
            "args": "run_id, item_id",
            "desc": "Full numbers for one item in the run",
        },
        {
            "name": "compare_items",
            "args": "run_id, item_id_a, item_id_b",
            "desc": "Side-by-side compare two items",
        },
        {
            "name": "list_expiry_capped",
            "args": "run_id",
            "desc": "Items whose order was capped by expiration",
        },
        {
            "name": "why_item",
            "args": "run_id, item_id",
            "desc": "Stored justification for an item",
        },
    ]


def call_tool(name: str, **kwargs: Any) -> dict[str, Any]:
    if name not in TOOL_REGISTRY:
        raise ValueError(f"Unknown tool '{name}'. Allowed: {sorted(TOOL_REGISTRY)}")
    return TOOL_REGISTRY[name](**kwargs)


def route_question(run_id: str, question: str) -> dict[str, Any]:
    """
    Tiny intent router — maps natural language to an approved tool.
    Never generates SQL.
    """
    q = (question or "").lower()
    if "expir" in q:
        return {"tool": "list_expiry_capped", "result": tool_list_expiry_capped(run_id)}
    if "compare" in q and " and " in q:
        # naive: "compare 1 and 2"
        parts = q.replace(",", " ").split()
        ids = [p for p in parts if p.isdigit()]
        if len(ids) >= 2:
            return {
                "tool": "compare_items",
                "result": tool_compare_items(run_id, ids[0], ids[1]),
            }
    if "why" in q or "justif" in q:
        parts = q.replace("#", " ").split()
        ids = [p for p in parts if p.isdigit()]
        if ids:
            return {"tool": "why_item", "result": tool_why_item(run_id, ids[0])}
    if "list" in q or "what" in q or "order" in q:
        return {"tool": "list_order_lines", "result": tool_list_order_lines(run_id)}
    return {"tool": "get_order_run", "result": tool_get_order_run(run_id)}
