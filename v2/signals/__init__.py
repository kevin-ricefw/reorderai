"""Regional / external demand signals (Okemos-scoped)."""

from v2.signals.regional_news import (
    REGION_LABEL,
    ingest_manual_signal,
    load_cached_signals,
    match_product_news_factor,
    refresh_signals_with_openai,
)

__all__ = [
    "REGION_LABEL",
    "ingest_manual_signal",
    "load_cached_signals",
    "match_product_news_factor",
    "refresh_signals_with_openai",
]
