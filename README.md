# Reorder AI (Wecomm)

Vendor + lead time (**L**) + days to cover (**C**) → buyer action list (ORDER / WATCH / SKIP) with ADS, ROP, cover target, full-case qty, festivals, and Excel.

| Doc | Contents |
|-----|----------|
| [`docs/W1_DETECT_ORDER_WORKFLOW.md`](docs/W1_DETECT_ORDER_WORKFLOW.md) | Detect-order math (current) |
| [`docs/EXAMPLE.md`](docs/EXAMPLE.md) | Worked SKU example |
| [`docs/COMPLETE_SYSTEM_WORKFLOW.md`](docs/COMPLETE_SYSTEM_WORKFLOW.md) | End-to-end system map + tables + APIs |
| [`docs/PHASE1_FORECASTING.md`](docs/PHASE1_FORECASTING.md) | Nightly ML + phases |
| [`deploy/TL_API_HANDOFF.md`](deploy/TL_API_HANDOFF.md) | Live API URL for TL |

```text
Sales CSVs + inventory CSV
        │
        ▼
import_local_to_paul.py  →  tenant DB
  (ai_pos_daily_sales, product_locations, product_vendor, …)
        │
        ▼
Nightly job (systemd 02:00 America/Detroit)
  → data/forecast_store/  (class, P50/P90, SKU uplift)
        │
        ▼
POST /api/detect-order   (X = L + C)
  ADS from live 90d sales only (never invent from P50)
  ROP = ADS×L + SS(L)           → urgency / WATCH trigger
  Desired = ceil(ADS×C×uplift + SS(C))
  Arrival = max(0, OH − ADS×L)
  qty = round UP to full cases
  ADS≈0 → SKIP
        │
        ▼
run_id → Excel · Streamlit (blue UI) · chatbot tools
```

**Live API:** `http://74.249.36.238:8000`  
**Demo UI:** Streamlit → set API base to that URL (or local uvicorn).

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env   # fill DB_* + TENANT_SCHEMA

# Optional local tunnel for laptop DB work:
# Bastion + SSH → 127.0.0.1:5433

python scripts/import_local_to_paul.py --execute   # reload sales/stock/vendors
python scripts/run_nightly_forecast.py --lookback-days 0

python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
python -m streamlit run demo_app/streamlit_app.py --server.port 8501
# or: run_demo.bat
```

- API docs: `/docs`
- Health: `GET /api/health` · DB: `GET /api/db-health`

---

## Order math (current)

| Step | Formula |
|------|---------|
| Window | **X = L + C** |
| ADS | units in last **90** days ÷ 90 (**sales only**; never derived from ML P50) |
| `ads_times_x` | ADS × X (display / audit) |
| ROP | ADS×L + SS(L) — **urgency only** |
| Stock at arrival | max(0, OH − ADS×L) |
| Desired / AI cover | ceil(ADS×C×uplift + SS(C)) |
| Qty | max(0, Desired − arrival), then **ceil to full cases** |
| Dead stock | ADS≈0 → **SKIP**, qty 0 |
| Actions | **ORDER** / **WATCH** / **SKIP** |
| Justification | Report-style template (no GPT) |

ML P50/P90 are **reference only** — they do not set order qty.

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
  "time_to_cover_days": 14,
  "uplift_types": ["weekend", "festival"],
  "risk_factor": 50
}
```

`uplift_types`: multi-select `weekend` | `festival` | `trend` (`[]` = no uplift).  
`risk_factor`: 0–100 (higher = more safety stock).

---

## Forecast + Azure schedule

```text
Nightly batch (VM systemd timer @ 02:00 America/Detroit)
  classify (Syntetos–Boylan)
  → Smooth: LightGBM / bootstrap
  → Intermittent: Croston-SBA + Monte Carlo P50/P90
  → Erratic/Lumpy: TSB + Monte Carlo
  → single-demand-day: rule
  → learn SKU weekend/festival uplift
  → overwrite data/forecast_store/

Detect-order reads those files (no live retrain per click).
SKU uplift applied at order time on cover sales (ADS×C).
```

Demand preference: local `Product Sales *.csv` → `ai_pos_daily_sales` → live `orders`×`order_items`.  
On the Azure VM nightly job: `FORECAST_USE_LOCAL_SALES=0` (DB sales).

Deploy units: `deploy/reorder-nightly-forecast.service` + `.timer` · install via `deploy/install-nightly-forecast.sh`.

---

## Repo map

| Path | Role |
|------|------|
| `api/services/reorder_engine.py` | ADS / SS / ROP / cover / cases / actions |
| `api/services/detect_order_service.py` | Detect-order orchestration + justification |
| `api/repositories/forecast_store.py` | Read batch P50/P90 + live ADS (no invent from P50) |
| `api/services/order_export.py` | Excel export |
| `demo_app/streamlit_app.py` | Streamlit demo (blue theme) |
| `v2/forecasting/` | Classification, Croston/TSB, LightGBM, festivals, uplift |
| `scripts/run_nightly_forecast.py` | Nightly batch |
| `scripts/import_local_to_paul.py` | Inventory + vendor map + sales → tenant |
| `deploy/` | VM systemd timer + TL handoff |

Local `data/` dumps are **not** committed.

---

## Env flags

```
TENANT_SCHEMA=wecomm_…
DETECT_ORDER_USE_LIVE_SQL=1
FORECAST_STORE_USE_BATCH=1
FORECAST_STORE_USE_LIVE_SQL=1
ADS_LOOKBACK_DAYS=90
FORECAST_USE_LOCAL_SALES=0          # VM: live DB; laptop may use auto/local CSVs
FORECAST_LOOKBACK_DAYS=0
SKU_UPLIFT_ENABLED=1
UPLIFT_ENABLED=0
REORDER_TZ=America/Detroit
```

---

## Tests

```bash
pytest -q
# contract: tests/test_reorder_math_contract.py
```
