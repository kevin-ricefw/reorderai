# Phases 1–3 — Implementation map (current)

## Phase 1 — MVP forecasting + detect-order

| Piece | Status | Location |
|-------|--------|----------|
| Syntetos–Boylan classify | Done | `v2/forecasting/syntetos_boylan.py` |
| Croston-SBA / TSB | Done | `v2/forecasting/croston.py` |
| Monte Carlo P50/P90 | Done | `v2/forecasting/monte_carlo.py` |
| Smooth LightGBM | Done (fallback bootstrap) | `v2/forecasting/smooth_lgbm.py` |
| Nightly batch | Done | `scripts/run_nightly_forecast.py` |
| forecast_store | Done | `data/forecast_store/` + `forecast_store_io.py` |
| Detect-order (cover-C, full cases, ORDER/WATCH/SKIP) | Done | `api/services/reorder_engine.py` + `detect_order_service.py` |
| ADS from sales only (no invent from P50) | Done | `api/repositories/forecast_store.py` |
| Report-style justification | Done | `_template_justification` (no GPT) |
| Expiry + last pallet | Done | `detect_order_repository.py` |
| Azure nightly timer | Done | `deploy/reorder-nightly-forecast.timer` @ 02:00 America/Detroit |

## Phase 2 — Festival / weekend uplift

| Piece | Status | Location |
|-------|--------|----------|
| India/US festival calendar | Done | `v2/forecasting/festival_calendar.py` |
| Per-SKU learned uplift | Done | `v2/forecasting/sku_uplift.py` |
| Apply at **order time** on ADS×C | Done | `ForecastStore._window_sku_uplift` |
| Toggle | `SKU_UPLIFT_ENABLED=1` | `.env` |
| Category-wide uplift (optional) | Off by default | `UPLIFT_ENABLED=0` · `uplift.py` |

## Phase 3 — Investigate chatbot

| Piece | Status | Location |
|-------|--------|----------|
| Fixed tools | Done | `api/services/chatbot_tools.py` |
| Scoped to run_id | Done | all tools require `run_id` |
| API | Done | `POST /api/chatbot/ask`, `/tool` |

No text-to-SQL. Tools only. No GPT required.
