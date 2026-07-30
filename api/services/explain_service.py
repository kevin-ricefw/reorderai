"""Step 6 — GPT justification (explains numbers; never decides qty)."""

from __future__ import annotations

import os
from typing import Any


def explain_reorder(question: str, context: dict[str, Any]) -> str:
    """
    Ask OpenAI to explain already-computed order numbers.
    Returns empty-ish / fallback text if no API key.
    """
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return ""

    try:
        from openai import OpenAI
    except ImportError:
        return ""

    facts = "\n".join(f"- {k}: {v}" for k, v in context.items() if v is not None)
    prompt = (
        f"{question}\n\nFacts (do not invent numbers):\n{facts}\n\n"
        "Reply in 2-4 short sentences. Do not change the order quantity."
    )
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You explain inventory reorder recommendations. "
                        "Use only provided facts. Never invent quantities."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=220,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text
    except Exception as exc:  # noqa: BLE001
        return f"OpenAI unavailable: {type(exc).__name__}"
