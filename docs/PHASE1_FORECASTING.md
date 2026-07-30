# Phases 1–3 — Implementation map

## Phase 1 — MVP forecasting + detect-order

| Piece | Status | Location |
|-------|--------|----------|
| Syntetos–Boylan classify | Done | `v2/forecasting/syntetos_boylan.py` |
| Croston-SBA / TSB | Done | `v2/forecasting/croston.py` |
| Monte Carlo P50/P90 | Done | `v2/forecasting/monte_carlo.py` |
| Smooth LightGBM | Done (fallback bootstrap) | `v2/forecasting/smooth_lgbm.py` |
| Nightly batch | Done | `scripts/run_nightly_forecast.py` |
| forecast_store | Done | `data/forecast_store/` + `forecast_store_io.py` |
| Detect-order Steps 1–6 | Done | `api/` |
| GPT justification | Done (needs OPENAI_API_KEY) | `api/services/explain_service.py` |
| Expiry + last pallet | Done | `detect_order_repository.py` |

## Phase 2 — Weather / festival uplift

| Piece | Status | Location |
|-------|--------|----------|
| Category multipliers | Done | `v2/forecasting/uplift.py` |
| Toggle | `UPLIFT_ENABLED=1` | `.env` |
| Apply in batch | Done | `pipeline.apply_uplift_to_forecasts` |

Validate factors against real event history before enabling in production.

## Phase 3 — Investigate chatbot

| Piece | Status | Location |
|-------|--------|----------|
| Fixed tools | Done | `api/services/chatbot_tools.py` |
| Scoped to run_id | Done | all tools require `run_id` |
| API | Done | `POST /api/chatbot/ask`, `/tool` |

No text-to-SQL. Tools only.
