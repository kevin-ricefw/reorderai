# Reorder AI (Wecomm)

Backend for **W-1 detect-order**: TL UI sends vendor + lead time + days to cover; this API returns the order list, stock math, and justification.

Phases 1–3 from the design doc are implemented as APIs + nightly batch (no storefront UI in this repo).

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env   # fill DB_* + optional OPENAI_API_KEY
# Start Bastion + SSH tunnel to 127.0.0.1:5433 first

python scripts/run_nightly_forecast.py   # Phase-1/2 batch → data/forecast_store/
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- API docs: http://localhost:8000/docs  
- DB check: `GET /api/db-health`

---

## TL UI endpoints

| Call | Purpose |
|------|---------|
| `GET /api/detect-order` | Vendor dropdown |
| `POST /api/detect-order` | `{vendor_id, lead_time_days, time_to_cover_days}` → order list |
| `GET /api/detect-order/runs/{run_id}` | Saved run (Decision 8) |
| `GET /api/chatbot/tools` | Approved chatbot tools |
| `POST /api/chatbot/ask` | `{run_id, question}` → tool-grounded answer |
| `POST /api/chatbot/tool` | Call a specific approved tool |

### Example

```http
POST /api/detect-order
{
  "vendor_id": "2",
  "lead_time_days": 5,
  "time_to_cover_days": 7
}
```

---

## Architecture (design doc)

```text
Nightly batch
  classify (Syntetos–Boylan)
  → Smooth: LightGBM (else bootstrap)
  → Intermittent: Croston-SBA + Monte Carlo P50/P90
  → Erratic/Lumpy: TSB + Monte Carlo
  → optional Phase-2 category uplift (UPLIFT_ENABLED=1)
  → data/forecast_store/

Detect-order API
  vendors / catalog / stock from Wecomm
  read P50/P90 from forecast_store
  order = P90 − stock (+ expiry cap, box round)
  GPT/template justification
  save run_id

Chatbot (Phase 3)
  scoped to run_id; fixed tools only — never free SQL
```

---

## Repo map

| Path | Role |
|------|------|
| `api/routes/detect_order.py` | W-1 API |
| `api/routes/chatbot.py` | Investigate chatbot |
| `api/repositories/` | Live Wecomm + forecast_store reads |
| `v2/forecasting/` | Classification, Croston/TSB, MC, LightGBM, uplift |
| `v2/inventory_math/` | SS / ROP / pack helpers |
| `scripts/run_nightly_forecast.py` | Nightly batch |
| `docs/` | Architecture + phase notes |

---

## Env flags

```
TENANT_SCHEMA=wecomm_019fafca-fa67-7393-84c4-4ec423f88c15
DETECT_ORDER_USE_LIVE_SQL=1
FORECAST_STORE_USE_BATCH=1
FORECAST_STORE_USE_LIVE_SQL=1
UPLIFT_ENABLED=0
OPENAI_API_KEY=
```

---

## Tests

```bash
pytest -q
```
