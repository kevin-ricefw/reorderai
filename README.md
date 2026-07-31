# Reorder AI (Wecomm)

Vendor + lead time (**L**) + days to cover (**C**) → reorder sheet with ADS, safety stock, ROP, ML P50/P90, AI target, cases, and Excel.

**Precise flow:** [`docs/W1_DETECT_ORDER_WORKFLOW.md`](docs/W1_DETECT_ORDER_WORKFLOW.md)  
**Worked example (AASHIRVAAD ATTA):** [`docs/EXAMPLE.md`](docs/EXAMPLE.md)  
**Full system map:** [`docs/COMPLETE_SYSTEM_WORKFLOW.md`](docs/COMPLETE_SYSTEM_WORKFLOW.md)

```text
Sales + inventory + vendor map  →  tenant DB
                │
                ▼
Nightly ML batch  →  data/forecast_store/  (class → P50/P90 + uplift)
                │
                ▼
POST /api/detect-order  (X = L + C only)
  ADS(90d) → SS → ROP flag
  AI_target = max(P90×uplift, ADS×X+SS)
  order = pack_round(AI_target − on_hand)   # case if need ≥ 80% pack
                │
                ▼
run_id → Excel export · Streamlit demo · chatbot tools
```

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env   # fill DB_* + optional OPENAI_API_KEY
# Bastion + SSH tunnel → 127.0.0.1:5433

python scripts/import_local_to_paul.py --execute   # optional: reload sales/stock/vendors
python scripts/run_nightly_forecast.py --lookback-days 0

python -m uvicorn api.main:app --host 127.0.0.1 --port 8001
python -m streamlit run demo_app/streamlit_app.py --server.port 8501
# or: run_demo.bat
```

- API docs: http://127.0.0.1:8001/docs  
- Demo UI: http://127.0.0.1:8501  
- DB check: `GET /api/db-health`

---

## Order math (summary)

| Step | Formula |
|------|---------|
| Window | **X = L + C** (no extra days) |
| ADS | units in last **90** days ÷ 90 |
| ROP | ADS×L + SS — **flag only** (`below_reorder_point`) |
| AI target | max(P90 for X × uplift, ADS×X + SS_X) |
| Qty | max(0, target − on-hand), then case round (≥80% pack) |

ROP false does **not** block an order when on-hand is below the full-window target.  
See [`docs/EXAMPLE.md`](docs/EXAMPLE.md).

---

## Workflow endpoints

| Call | Purpose |
|------|---------|
| `GET /api/detect-order` | Vendor list |
| `POST /api/detect-order` | `{vendor_id, lead_time_days, time_to_cover_days}` → order list |
| `GET /api/detect-order/runs/{run_id}` | Saved run |
| `GET /api/detect-order/runs/{run_id}/export.xlsx` | Excel order sheet |
| `GET /api/chatbot/tools` | Approved chatbot tools |
| `POST /api/chatbot/ask` | `{run_id, question}` → tool-grounded answer |

### Example request

```http
POST /api/detect-order
{
  "vendor_id": "18",
  "lead_time_days": 3,
  "time_to_cover_days": 14
}
```

---

## Forecast pipeline

```text
Nightly batch
  classify (Syntetos–Boylan)
  → Smooth: LightGBM
  → Intermittent: Croston-SBA + Monte Carlo P50/P90
  → Erratic/Lumpy: TSB + Monte Carlo
  → single-demand-day: rule
  → per-SKU weekend/festival uplift
  → data/forecast_store/

Detect-order reads those files (no live retrain per click).
```

Demand preference: local `Product Sales *.csv` → `ai_pos_daily_sales` → live orders.

---

## Repo map

| Path | Role |
|------|------|
| `api/services/reorder_engine.py` | ADS / SS / ROP / AI target / cases |
| `api/services/detect_order_service.py` | Detect-order orchestration |
| `api/services/order_export.py` | Excel export |
| `demo_app/streamlit_app.py` | Streamlit demo |
| `v2/forecasting/` | Classification, Croston/TSB, LightGBM, uplift |
| `scripts/run_nightly_forecast.py` | Nightly batch |
| `scripts/import_local_to_paul.py` | Inventory + vendor map + sales → tenant |
| `docs/EXAMPLE.md` | AASHIRVAAD ATTA worked example |

Local `data/` dumps are **not** committed.

---

## Env flags

```
TENANT_SCHEMA=wecomm_…
DETECT_ORDER_USE_LIVE_SQL=1
FORECAST_STORE_USE_BATCH=1
FORECAST_STORE_USE_LIVE_SQL=1
ADS_LOOKBACK_DAYS=90
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
