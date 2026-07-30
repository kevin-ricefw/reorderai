"""Okemos / 48864 regional demand signals (NOT USA-wide).

Uses OpenAI to turn Mid-Michigan / Okemos / East Lansing / Ingham headlines
into capped category impact multipliers (e.g. fly disease → lentils down).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.data_paths import PROJECT_ROOT

CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "okemos_regional_signals.json"

REGION_LABEL = "Okemos, MI 48864 / East Lansing / Ingham County / Mid-Michigan"
REGION_KEYWORDS = (
    "okemos",
    "48864",
    "east lansing",
    "lansing",
    "ingham",
    "mid-michigan",
    "mid michigan",
    "meridian township",
    "haslett",
    "williamston",
)

# Soft caps — never wipe or explode demand from a headline
MIN_FACTOR = 0.70
MAX_FACTOR = 1.25

# Seed / demo local signals when no live API call (tests + offline)
DEFAULT_SIGNALS: list[dict[str, Any]] = []


def _is_regional_text(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in REGION_KEYWORDS)


def load_cached_signals() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {"updated_at": None, "region": REGION_LABEL, "signals": []}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": None, "region": REGION_LABEL, "signals": []}


def save_signals(payload: dict[str, Any]) -> Path:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["region"] = REGION_LABEL
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return CACHE_PATH


def clamp_factor(factor: float) -> float:
    return float(min(MAX_FACTOR, max(MIN_FACTOR, float(factor))))


def match_product_news_factor(
    *,
    description: str,
    department: str = "",
    signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Return capped demand factor for a product from regional signals.

    Multiplies only when product/category keywords match. Default 1.0.
    """
    payload = load_cached_signals() if signals is None else {"signals": signals}
    sigs = payload.get("signals") or []
    text = f"{description} {department}".lower()
    applied = 1.0
    notes: list[str] = []
    matched: list[dict[str, Any]] = []

    for sig in sigs:
        if not isinstance(sig, dict):
            continue
        # Reject non-regional payloads if they slipped in
        scope = str(sig.get("region_scope") or sig.get("scope") or "").lower()
        headline = str(sig.get("headline") or sig.get("summary") or "")
        if scope and not _is_regional_text(scope) and scope not in ("local", "okemos", "mid-michigan"):
            if not _is_regional_text(headline):
                continue
        keywords = sig.get("product_keywords") or sig.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [keywords]
        hit = False
        for kw in keywords:
            kw_s = str(kw).lower().strip()
            if kw_s and kw_s in text:
                hit = True
                break
        if not hit:
            continue
        factor = clamp_factor(float(sig.get("demand_factor") or 1.0))
        applied *= factor
        matched.append(sig)
        notes.append(
            f"{sig.get('headline') or sig.get('summary') or 'local signal'}: ×{factor:.2f}"
        )

    applied = clamp_factor(applied)
    return {
        "news_factor": round(applied, 3),
        "news_signal": "; ".join(notes) if notes else "",
        "news_matched": matched,
        "region": REGION_LABEL,
    }


def ingest_manual_signal(
    *,
    headline: str,
    product_keywords: list[str],
    demand_factor: float,
    summary: str = "",
) -> dict[str, Any]:
    """Append a manager-provided Okemos-scoped signal (e.g. lentil fly disease)."""
    if not _is_regional_text(headline + " " + summary + " okemos 48864"):
        # Force regional scope tag even if headline omits place — caller must be local
        pass
    sig = {
        "headline": headline.strip(),
        "summary": summary.strip() or headline.strip(),
        "product_keywords": [str(k).lower().strip() for k in product_keywords if str(k).strip()],
        "demand_factor": clamp_factor(demand_factor),
        "region_scope": REGION_LABEL,
    }
    payload = load_cached_signals()
    signals = list(payload.get("signals") or [])
    signals.append(sig)
    payload["signals"] = signals
    save_signals(payload)
    return sig


def refresh_signals_with_openai(
    *,
    headlines: list[str] | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Ask OpenAI to extract Okemos-only category impacts from provided headlines.

    If no headlines, keeps cache unchanged (does not invent USA news).
    """
    key = api_key or os.getenv("OPENAI_API_KEY", "").strip()
    texts = [h.strip() for h in (headlines or []) if h and str(h).strip()]
    # Keep only regionally relevant lines
    local = [t for t in texts if _is_regional_text(t)]
    if not local:
        return load_cached_signals()

    if not key:
        # Offline: keyword heuristic for lentil/disease style headlines
        signals = []
        for line in local:
            low = line.lower()
            kws: list[str] = []
            factor = 1.0
            if any(w in low for w in ("lentil", "dal", "pulse", "legume")):
                kws.extend(["lentil", "dal", "toor", "moong", "masoor", "chana"])
            if any(w in low for w in ("disease", "fly", "pest", "recall", "outbreak")):
                factor = 0.85
            if any(w in low for w in ("shortage", "scarce")):
                factor = min(factor, 0.80)
            if any(w in low for w in ("festival", "diwali", "holi")) and "okemos" in low:
                factor = max(factor, 1.10)
            if kws:
                signals.append(
                    {
                        "headline": line[:200],
                        "summary": line[:400],
                        "product_keywords": kws,
                        "demand_factor": clamp_factor(factor),
                        "region_scope": REGION_LABEL,
                    }
                )
        payload = {"signals": signals, "source": "heuristic"}
        save_signals(payload)
        return payload

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        prompt = (
            f"You extract LOCAL grocery demand impacts for {REGION_LABEL} ONLY. "
            "Ignore national USA news unless it explicitly names this region. "
            "Return JSON: {\"signals\":[{\"headline\":str,\"summary\":str,"
            "\"product_keywords\":[str],\"demand_factor\":float,\"region_scope\":str}]}. "
            "demand_factor between 0.70 and 1.25 (1.0 = no change). "
            "Example: fly disease affecting lentils in Okemos → keywords lentil/dal, factor 0.85.\n\n"
            "Headlines:\n" + "\n".join(f"- {t}" for t in local)
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Return only valid JSON. Mid-Michigan scope only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )
        content = resp.choices[0].message.content or "{}"
        m = re.search(r"\{.*\}", content, flags=re.S)
        data = json.loads(m.group(0) if m else content)
        signals = []
        for sig in data.get("signals") or []:
            scope = str(sig.get("region_scope") or "")
            if scope and not _is_regional_text(scope) and "okemos" not in scope.lower():
                sig["region_scope"] = REGION_LABEL
            sig["demand_factor"] = clamp_factor(float(sig.get("demand_factor") or 1.0))
            sig["product_keywords"] = [
                str(k).lower().strip() for k in (sig.get("product_keywords") or []) if str(k).strip()
            ]
            if sig["product_keywords"]:
                signals.append(sig)
        payload = {"signals": signals, "source": "openai"}
        save_signals(payload)
        return payload
    except Exception as exc:
        payload = load_cached_signals()
        payload["error"] = str(exc)
        return payload
