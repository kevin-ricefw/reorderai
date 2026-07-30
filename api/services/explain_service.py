"""OpenAI explainability for vendor reorder recommendations."""

from __future__ import annotations

import json
import os
from typing import Any


SYSTEM_PROMPT = """You are the Inventory AI reorder explainability assistant for an Indian grocery
store in Okemos, MI (48864). Explain vendor reorder recommendations in plain English AND show
the underlying formulas with intermediate numbers from the provided JSON context.

Rules:
- Use only the numbers in context; do not invent sales history.
- Regional news impacts apply only to Okemos / Mid-Michigan — never USA-wide assumptions.
- Formula path: ADS, safety stock (Z=1.65), AI Min = (ADS × lead) + SS.
- Hybrid ML path: need = max(formula_need, forecast_horizon − stock), then pack round.
- Formula path is the floor inside Hybrid (not a separate mode)
- Syntetos-Boylan softens ML for intermittent/lumpy items
- Okemos / 48864 news signals apply soft category factors only
- Order qty rounded to case/box (50% fill rule)
"""


def build_explain_context(row: dict[str, Any] | None, *, vendor: str = "") -> dict[str, Any]:
    """Normalize a reorder row into chat context."""
    if not row:
        return {"vendor": vendor, "note": "No product selected"}
    keys = [
        "upc",
        "description",
        "vendor_name",
        "current_stock",
        "ads",
        "demand_std",
        "safety_stock",
        "ai_min",
        "lead_time_days",
        "days_to_cover",
        "forecast_horizon_days",
        "planning_cover_days",
        "lookback_days",
        "formula_raw_need",
        "ml_forecast_demand",
        "ml_need",
        "units_needed",
        "order_qty",
        "cases_to_order",
        "pack_size",
        "pack_rounding_note",
        "demand_class",
        "adi",
        "cv2",
        "news_factor",
        "news_signal",
        "confidence_score",
        "order_math_note",
        "strategy_mode",
        "formula_order_qty",
        "hybrid_order_qty",
    ]
    ctx = {k: row.get(k) for k in keys if k in row or True}
    ctx["vendor"] = vendor or row.get("vendor_name") or ""
    return {k: v for k, v in ctx.items() if v is not None and v != ""}


def explain_reorder(
    question: str,
    context: dict[str, Any],
    *,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
) -> str:
    """Answer a manager question using OpenAI + structured reorder context."""
    key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
    q = (question or "").strip()
    if not q:
        return "Ask a question about this recommendation (e.g. why this qty?)."

    if not key:
        return _offline_explain(q, context)

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Context JSON:\n"
                        + json.dumps(context, default=str)[:12000]
                        + "\n\nQuestion: "
                        + q
                    ),
                },
            ],
            temperature=0.2,
        )
        return (resp.choices[0].message.content or "").strip() or _offline_explain(q, context)
    except Exception as exc:
        return f"(OpenAI unavailable: {exc})\n\n" + _offline_explain(q, context)


def _offline_explain(question: str, context: dict[str, Any]) -> str:
    """Deterministic fallback when API key missing or call fails."""
    desc = context.get("description") or context.get("upc") or "this item"
    ads = context.get("ads")
    ai_min = context.get("ai_min")
    stock = context.get("current_stock")
    formula_need = context.get("formula_raw_need")
    ml_need = context.get("ml_need")
    order_qty = context.get("order_qty")
    pack = context.get("pack_size")
    cases = context.get("cases_to_order")
    horizon = context.get("forecast_horizon_days") or context.get("planning_cover_days")
    demand_class = context.get("demand_class")
    news = context.get("news_signal") or "none"
    q = question.lower()

    lines = [
        f"**{desc}**",
        f"- ADS={ads}, AI Min={ai_min}, stock={stock}",
        f"- Formula need={formula_need}, ML need={ml_need}, horizon={horizon}d",
        f"- Order={order_qty} units ({cases} case(s) × pack {pack})",
        f"- Demand class={demand_class}; news={news}",
    ]
    if "math" in q or "formula" in q or "calculation" in q:
        lines.append(
            "- Formula: AI Min = (ADS × lead/cover) + 1.65×σ×√lead; "
            "need = max(0, AI Min − stock); Hybrid = max(formula, forecast − stock); "
            "then pack round (50% fill)."
        )
    if "case" in q or "pack" in q or "round" in q:
        lines.append(f"- Pack note: {context.get('pack_rounding_note') or 'case rounding applied'}")
    if "news" in q or "disease" in q or "lentil" in q:
        lines.append(
            f"- Regional news (Okemos/48864 only): factor={context.get('news_factor', 1.0)}; {news}"
        )
    if "why" in q and "not" in q:
        lines.append(
            "- Not recommended when order_qty=0 (stock above AI Min and forecast covered)."
        )
    return "\n".join(lines)
