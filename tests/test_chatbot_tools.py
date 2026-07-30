from __future__ import annotations

import json

from api.services import chatbot_tools
from api.services.order_run_store import ORDER_RUNS_DIR


def _seed_run() -> str:
    payload = {
        "run_id": "run_test_chat_001",
        "vendor": {"vendor_id": "2", "vendor_name": "Test Vendor"},
        "lead_time_days": 3,
        "time_to_cover_days": 4,
        "x_days": 7,
        "order_line_count": 1,
        "total_units_to_order": 10,
        "items": [
            {
                "item_id": "101",
                "description": "Item A",
                "qty_to_order": 10,
                "available_stock": 2,
                "p50_demand": 8,
                "p90_demand": 12,
                "demand_class": "intermittent",
                "expiry_capped": True,
                "expiration_days_remaining": 5,
                "expiry_cap_days": 5,
                "box_qty": 1,
                "justification": "Order 10 because P90 is 12 and stock is 2.",
            },
            {
                "item_id": "102",
                "description": "Item B",
                "qty_to_order": 0,
                "available_stock": 20,
                "p50_demand": 3,
                "p90_demand": 5,
                "demand_class": "smooth",
                "expiry_capped": False,
                "box_qty": 1,
                "justification": "No order needed.",
            },
        ],
    }
    # write directly so path exists even if helper changes id
    ORDER_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = ORDER_RUNS_DIR / f"{payload['run_id']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload["run_id"]


def test_list_tools():
    tools = chatbot_tools.list_tools()
    names = {t["name"] for t in tools}
    assert "why_item" in names
    assert "list_order_lines" in names


def test_why_and_expiry_tools():
    run_id = _seed_run()
    why = chatbot_tools.tool_why_item(run_id, "101")
    assert "P90" in why["justification"] or "10" in why["justification"]
    capped = chatbot_tools.tool_list_expiry_capped(run_id)
    assert capped["count"] == 1
    assert capped["items"][0]["item_id"] == "101"


def test_route_question():
    run_id = _seed_run()
    out = chatbot_tools.route_question(run_id, "why is item 101 ordering so much?")
    assert out["tool"] == "why_item"
