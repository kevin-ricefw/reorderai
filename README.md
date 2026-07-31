# Reorder AI (Wecomm)

Detect-order API: vendor + lead time + days to cover → order list, stock math, and justification.

**Full system map (folders, file connections, ML, endpoints):**  
[`docs/COMPLETE_SYSTEM_WORKFLOW.md`](docs/COMPLETE_SYSTEM_WORKFLOW.md)

```text
Local / tenant demand history
        │
        ▼
Nightly forecast batch  →  data/forecast_store/ (P50/P90)
        │
        ▼
POST /api/detect-order  →  order lines + run_id
        │
        ▼
Chatbot tools (scoped to run_id)
```

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env   # fill DB_* + optional OPENAI_API_KEY
# Start Bastion + SSH tunnel to 127.0.0.1:5433 first

python scripts/run_nightly_forecast.py   # forecast batch → data/forecast_store/
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- API docs: http://localhost:8000/docs  
- DB check: `GET /api/db-health`

---

## Workflow endpoints

| Call | Purpose |
|------|---------|
| `GET /api/detect-order` | Vendor list |
| `POST /api/detect-order` | `{vendor_id, lead_time_days, time_to_cover_days}` → order list |
| `GET /api/detect-order/runs/{run_id}` | Saved run |
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

## Forecast pipeline

```text
Nightly batch
  classify (Syntetos–Boylan)
  → Smooth: LightGBM (else bootstrap)
  → Intermittent: Croston-SBA + Monte Carlo P50/P90
  → Erratic/Lumpy: TSB + Monte Carlo
  → per-SKU weekend/festival uplift (learned; SKU_UPLIFT_ENABLED=1)
  → optional category uplift (UPLIFT_ENABLED=1)
  → data/forecast_store/

Detect-order
  vendors / catalog / stock from Wecomm
  read P50/P90 from forecast_store
  order = P90 − stock (+ expiry cap, box round)
  justification
  save run_id

Chatbot
  scoped to run_id; fixed tools only
```

Demand preference for the batch: local dated POS sales (if present) → `ai_pos_daily_sales` → live `orders`.

---

## Repo map

| Path | Role |
|------|------|
| `api/routes/detect_order.py` | Detect-order API |
| `api/routes/chatbot.py` | Investigate chatbot |
| `api/repositories/` | Wecomm + forecast_store reads |
| `v2/forecasting/` | Classification, Croston/TSB, MC, LightGBM, uplift |
| `v2/inventory_math/` | SS / ROP / pack helpers |
| `scripts/run_nightly_forecast.py` | Nightly batch |
| `scripts/import_local_to_paul.py` | Optional local → tenant import |
| `docs/COMPLETE_SYSTEM_WORKFLOW.md` | Full workflow + folder/file map |
| `docs/` | Architecture + phase notes |

Local `data/` dumps (sales, inventory, vendors) are **not** committed.

---

## Env flags

```
TENANT_SCHEMA=wecomm_019fafca-fa67-7393-84c4-4ec423f88c15
DETECT_ORDER_USE_LIVE_SQL=1
FORECAST_STORE_USE_BATCH=1
FORECAST_STORE_USE_LIVE_SQL=1
FORECAST_USE_LOCAL_SALES=auto
FORECAST_LOOKBACK_DAYS=0
SKU_UPLIFT_ENABLED=1
UPLIFT_ENABLED=0
OPENAI_API_KEY=
```

---

## Tests

```bash
pytest -q
```
