# Reorder AI — Complete System Workflow

This document explains **every folder**, **every important file**, how they **connect**, how the **workflow flows**, what **ML** does, and what each **API endpoint** serves.

---

## 1. Big picture (how the product works)

```text
┌─────────────────────────────────────────────────────────────────┐
│  A) DATA LAYER                                                  │
│     Local data/  OR  Paul Wecomm Postgres (tenant schema)         │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  B) NIGHTLY ML BATCH                                            │
│     scripts/run_nightly_forecast.py                             │
│     → classify demand → pick model → P50/P90 → forecast_store   │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  C) DETECT-ORDER API (UI calls this)                            │
│     vendor + L + C → order list + justification + run_id        │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  D) CHATBOT API (investigate one run_id only)                   │
│     fixed tools — no free SQL                                   │
└─────────────────────────────────────────────────────────────────┘
```

**User-facing flow**

1. Store / UI picks a **vendor**.
2. UI sets **lead time (L)** and **days to cover (C)** — dynamic every request.
3. UI calls `POST /api/detect-order`.
4. API loads that vendor’s catalog + stock from Wecomm.
5. API reads **P90** demand for window `X = L + C` from nightly forecast files.
6. For each SKU: `qty_to_order = round_up_to_pack(max(0, P90 − stock))` (+ expiry rules).
7. API saves a **`run_id`** and returns the order list.
8. Chatbot can answer questions **only about that `run_id`**.

---

## 2. Repository tree (what each folder is for)

```text
inventory-ai/
├── api/                 FastAPI HTTP layer (endpoints)
├── config/              Paths + DB settings from .env
├── core/                Shared logging helper
├── database/            Wecomm Postgres connector + tenant schema
├── v2/
│   ├── forecasting/     ML: classify → models → P50/P90 → uplift
│   └── inventory_math/  Formulas (SS, ROP, pack round, EOQ)
├── scripts/             Runnable jobs (nightly batch, import/export)
├── tests/               Unit tests
├── docs/                Design + this workflow doc
├── data/                Local dumps + runtime outputs (NOT in git)
├── README.md            Short quick start
├── requirements.txt     Python dependencies
├── .env.example         Env template (copy to .env)
└── .gitignore           Ignores secrets + data dumps
```

---

## 3. Folder-by-folder and file-by-file

### 3.1 `api/` — HTTP API (what the UI talks to)

| File | Purpose |
|------|---------|
| `api/main.py` | Creates FastAPI app, CORS, mounts routers, `GET /` lists endpoints |
| `api/routes/system.py` | Health checks |
| `api/routes/detect_order.py` | Detect-order HTTP routes → calls service |
| `api/routes/chatbot.py` | Chatbot HTTP routes → calls tools |
| `api/schemas/detect_order.py` | Request/response models (Pydantic) for detect-order |
| `api/schemas/chatbot.py` | Request/response models for chatbot |
| `api/services/detect_order_service.py` | **Core W-1 business logic** (stock math, P90, pack, save run) |
| `api/services/explain_service.py` | Optional GPT justification (needs `OPENAI_API_KEY`) |
| `api/services/order_run_store.py` | Save/load `run_id` JSON under `data/cache/order_runs/` |
| `api/services/chatbot_tools.py` | Fixed read-only tools scoped to one `run_id` |
| `api/repositories/detect_order_repository.py` | Live SQL: vendors, catalog, stock, expiry, last pallet |
| `api/repositories/forecast_store.py` | Read P50/P90 from batch files (or ADS SQL fallback) |

**Connection pattern**

```text
routes/*.py  →  services/*.py  →  repositories/*.py  →  database/connectors/wecomm.py
                              └→  order_run_store / explain_service
```

---

### 3.2 `config/` — settings and paths

| File | Purpose |
|------|---------|
| `config/settings.py` | Loads `DB_*` from `.env` into typed settings |
| `config/data_paths.py` | Central paths: `data/sales`, `inventory`, `vendors`, `forecast_store`, `cache`, etc. |

Lead time is **not** a file path — L/C come from the API body.

---

### 3.3 `database/` — Wecomm connection

| File | Purpose |
|------|---------|
| `database/connectors/wecomm.py` | SQLAlchemy engine to Azure Postgres (via SSH tunnel `127.0.0.1:5433`) |
| `database/connectors/guard.py` | Safety helpers around DB access |
| `database/tenant.py` | Resolves `TENANT_SCHEMA` (Paul = `wecomm_019fafca-…`) and quotes identifiers |

All live queries use: `{TENANT_SCHEMA}.vendors`, `.products`, `.product_vendor`, `.product_locations`, `.product_barcodes`, `.orders`, etc.

---

### 3.4 `v2/forecasting/` — ML pipeline

| File | Purpose |
|------|---------|
| `sales_loader.py` | Load daily demand: local POS CSVs → `ai_pos_daily_sales` → Paul `orders` |
| `local_pos_sales.py` | Parse `Product Sales JAN 7.csv` style files into dated UPC qty series |
| `syntetos_boylan.py` | Classify each SKU: smooth / intermittent / erratic / lumpy |
| `smooth_lgbm.py` | **Smooth** model: pooled LightGBM (fallback: bootstrap) |
| `croston.py` | Croston-SBA + TSB parameter fits |
| `monte_carlo.py` | Simulate horizon demand → **P50 / P90** |
| `uplift.py` | Optional category festival/weather multipliers (`UPLIFT_ENABLED=1`) |
| `pipeline.py` | Orchestrates: classify → fit LightGBM → forecast each SKU → uplift |
| `forecast_store_io.py` | Write/read parquet/csv artifacts under `data/forecast_store/` |

**What ML learns from**

| Input | Used for ML? |
|-------|----------------|
| Daily sales qty by item/date | **Yes** — only demand training signal |
| Inventory on-hand | No — used at detect-order time |
| Past invoices folder | **No** — not wired into training today |
| Vendor POs in DB | Side reference (`last_pallet_qty`), not model training |

---

### 3.5 `v2/inventory_math/` — formulas

| File | Purpose |
|------|---------|
| `safety_stock.py` | Safety stock formula helpers |
| `reorder_point.py` | ROP helpers |
| `pack_size.py` | Round order qty up to case/pack |
| `economic_order_quantity.py` | EOQ helper (available; detect-order primarily uses P90 − stock) |

Detect-order’s main size rule is in `detect_order_service.py` (P90 − available + box round + expiry).

---

### 3.6 `scripts/` — batch jobs

| File | Purpose |
|------|---------|
| `run_nightly_forecast.py` | **Nightly job**: load demand → run pipeline → write `data/forecast_store/` |
| `import_local_to_paul.py` | Push local inventory/sales/vendors into Paul tables (AI sales + stock + product_vendor) |
| `export_paul_to_data.py` | Pull Paul tables out to local CSVs for offline work |

---

### 3.7 `data/` — local dumps + runtime (gitignored contents)

| Folder | Purpose |
|--------|---------|
| `sales/` | Daily POS `Product Sales *.csv` (demand history for ML) |
| `inventory/` | `current inventory count.csv` (on-hand by UPC) |
| `vendors/` | Vendor catalog workbooks (reference / import source) |
| `Past Invoices/` | Invoice files (stored; **not** used by ML today) |
| `GIFTCARD/` | Gift card export (not in ML path) |
| `forecast_store/` | **Nightly ML output** — P50/P90 files API reads |
| `cache/order_runs/` | Saved detect-order runs (`run_id` JSON) |
| `sandbox_exports/` | Optional DB export samples |
| `waste/` | Optional waste dumps |

Only `.gitkeep` + `data/README.md` are committed — not the CSVs/xlsx.

---

### 3.8 `docs/` — documentation

| File | Purpose |
|------|---------|
| `COMPLETE_SYSTEM_WORKFLOW.md` | **This file** — full system map |
| `W1_DETECT_ORDER_WORKFLOW.md` | Detect-order API contract |
| `PHASE1_FORECASTING.md` | Forecasting phase notes |
| `REORDER_AI_DATABASE_ARCHITECTURE.md` | Wecomm/Paul table map |

---

### 3.9 `tests/` — automated checks

| File | Purpose |
|------|---------|
| `test_forecast_pipeline.py` | Classification + pipeline smoke |
| `test_local_pos_sales.py` | Filename date parsing / calendar flags |
| `test_uplift.py` | Uplift on/off behavior |
| `test_chatbot_tools.py` | Tools scoped to run_id |
| `test_pack_size.py` / `test_inventory_math.py` | Math helpers |

---

### 3.10 `core/`

| File | Purpose |
|------|---------|
| `core/logger.py` | Shared logger factory |

---

## 4. How files connect (call graph)

### 4.1 Nightly ML batch

```text
scripts/run_nightly_forecast.py
        │
        ├─► v2/forecasting/sales_loader.py
        │         ├─► local_pos_sales.py          (data/sales/*.csv)
        │         ├─► ai_pos_daily_sales (Paul)   (if imported)
        │         └─► orders × order_items (Paul) (fallback)
        │
        ├─► v2/forecasting/pipeline.py
        │         ├─► syntetos_boylan.py          (class per SKU)
        │         ├─► smooth_lgbm.py              (smooth → LightGBM)
        │         ├─► croston.py + monte_carlo.py (intermittent / erratic / lumpy)
        │         └─► uplift.py                   (optional)
        │
        └─► v2/forecasting/forecast_store_io.py
                  └─► writes data/forecast_store/
```

### 4.2 Detect-order request

```text
UI / Postman
   POST /api/detect-order  {vendor_id, lead_time_days, time_to_cover_days}
        │
        ▼
api/routes/detect_order.py
        │
        ▼
api/services/detect_order_service.py
        │
        ├─► DetectOrderRepository
        │         └─► WecommDatabaseConnector
        │               vendors, product_vendor, product_locations,
        │               product_barcodes, product_batches, vendor_order_products
        │
        ├─► ForecastStore
        │         └─► reads data/forecast_store/  (P50/P90)
        │               (fallback: ADS from live sales SQL)
        │
        ├─► pack / expiry math in service
        ├─► explain_service.py  (optional GPT text)
        └─► order_run_store.py  → data/cache/order_runs/{run_id}.json
```

### 4.3 Chatbot

```text
POST /api/chatbot/ask  {run_id, question}
        │
        ▼
api/services/chatbot_tools.py
        │
        └─► load_order_run(run_id) only
              (cannot query arbitrary SQL / other vendors)
```

---

## 5. ML routing (how a model is chosen)

For each SKU, Syntetos–Boylan computes:

- **ADI** — average days between sales  
- **CV2** — variability of sale sizes  

| Class | Rule (approx) | Model |
|-------|----------------|--------|
| **Smooth** | ADI &lt; 1.32 and CV2 &lt; 0.49 | **LightGBM** (pooled); else bootstrap |
| **Intermittent** | ADI ≥ 1.32 and CV2 &lt; 0.49 | Croston-SBA + Monte Carlo |
| **Erratic** | ADI &lt; 1.32 and CV2 ≥ 0.49 | TSB + Monte Carlo |
| **Lumpy** | ADI ≥ 1.32 and CV2 ≥ 0.49 | TSB + Monte Carlo |

Outputs for horizons **7 / 14 / 21 / 30 / 45** days:

- **P50** — typical demand over the horizon  
- **P90** — high/safe demand (ordering target)

Detect-order uses **P90** for `X = L + C` (scaled from nearest stored horizon).

---

## 6. Detect-order math (per SKU)

Example inputs from UI: `L=5`, `C=7` → `X=12`.

```text
1. available_stock     ← product_locations
2. p90_X               ← forecast_store (scaled to X days)
3. raw_need            = max(0, p90_X − available_stock)
4. if expiry sooner than X → shrink coverage window
5. qty_to_order        = round up raw_need to box/pack size
6. justification       = template and/or GPT
```

---

## 7. API endpoints (what each serves)

Base URL (local): `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

| Method | Path | Serves |
|--------|------|--------|
| `GET` | `/` | Service info + endpoint index |
| `GET` | `/api/health` | Process alive (`{"status":"ok"}`) |
| `GET` | `/api/db-health` | Can we reach Wecomm Postgres? |
| `GET` | `/api/detect-order` | Vendor list (no vendor) **or** detect-order via query params |
| `POST` | `/api/detect-order` | **Main W-1**: vendor + L + C → order lines + `run_id` |
| `GET` | `/api/detect-order/runs/{run_id}` | Reload a saved order run |
| `GET` | `/api/chatbot/tools` | List approved chatbot tools |
| `POST` | `/api/chatbot/ask` | Route a natural question → one approved tool on `run_id` |
| `POST` | `/api/chatbot/tool` | Call a specific tool by name on `run_id` |

### 7.1 `POST /api/detect-order` body

```json
{
  "vendor_id": "16",
  "lead_time_days": 5,
  "time_to_cover_days": 7,
  "include_zero_orders": false,
  "generate_justification": true
}
```

### 7.2 Important response fields (per item)

| Field | Meaning |
|-------|---------|
| `item_id` | Wecomm product id |
| `description` | Product name |
| `available_stock` | On-hand |
| `demand_class` | smooth / intermittent / … |
| `p50_demand` / `p90_demand` | Forecast for window X |
| `qty_to_order` | Final order quantity |
| `box_qty` | Pack/case size used for rounding |
| `justification` | Why this line |
| `run_id` (top-level) | Saved run for chatbot |

### 7.3 Chatbot tools (approved only)

| Tool | Purpose |
|------|---------|
| `get_order_run` | Summary of the run |
| `list_order_lines` | Lines (optionally only nonzero) |
| `get_item_details` | One item from the run |
| `compare_items` | Compare two items in the run |
| `why_item` | Justification / drivers for one item |

---

## 8. Environment flags (`.env`)

| Variable | Role |
|----------|------|
| `DB_HOST` / `DB_PORT` / `DB_PASSWORD`… | Wecomm via SSH tunnel |
| `TENANT_SCHEMA` | Paul schema name |
| `DETECT_ORDER_USE_LIVE_SQL=1` | Live catalog/stock from DB |
| `FORECAST_STORE_USE_BATCH=1` | Prefer nightly P50/P90 files |
| `FORECAST_STORE_USE_LIVE_SQL=1` | ADS fallback if batch missing |
| `FORECAST_USE_LOCAL_SALES=auto` | Prefer local POS CSVs for batch |
| `UPLIFT_ENABLED=0` | Festival/weather uplift off until validated |
| `OPENAI_API_KEY` | Optional GPT justifications |

---

## 9. How to run the full system

```bash
# 1) Install
pip install -r requirements.txt
cp .env.example .env   # fill secrets

# 2) Tunnel to Wecomm (Bastion + SSH) so 127.0.0.1:5433 works

# 3) Nightly / on-demand forecast batch
python scripts/run_nightly_forecast.py

# 4) API
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 5) UI / curl calls POST /api/detect-order with vendor + L + C
```

Optional data sync:

```bash
python scripts/import_local_to_paul.py           # dry-run
python scripts/import_local_to_paul.py --execute # write vendors/stock/AI sales
```

---

## 10. What is *not* in the ML path (today)

| Asset | Status |
|-------|--------|
| `data/Past Invoices/` | Stored only; **not** training features |
| Gift cards | Not used by reorder ML |
| Delivery schedule Excel | Removed — L/C are dynamic from UI |
| Live POS `orders` rewrite | Import writes `ai_pos_daily_sales`, does not wipe POS checkouts |

---

## 11. End-to-end example (one product)

**LX COLD PRESSED SESAME OIL 2LT** (vendor HOS, stock 3, pack 6)

1. Nightly: sales sparse → class **intermittent** → **Croston-SBA** → P90(14d)≈6  
2. UI: vendor=HOS, L=5, C=7 → X=12  
3. API scales P90 ≈ 5.1, stock=3 → need ≈ 2.1 → round to pack **6**  
4. Returns line + `run_id`  
5. Chatbot can explain that line via `why_item` on the same `run_id`

**Smooth contrast:** high-frequency produce (e.g. Thai chilli) → class **smooth** → **LightGBM** on the full nightly batch (not bootstrap), because many smooth SKUs train together.

---

## 12. Quick “who owns what”

| Concern | Owner file(s) |
|---------|----------------|
| HTTP surface | `api/routes/*`, `api/main.py` |
| Order business rules | `api/services/detect_order_service.py` |
| Live DB reads | `api/repositories/detect_order_repository.py` |
| Forecast reads | `api/repositories/forecast_store.py` |
| Demand load | `v2/forecasting/sales_loader.py` |
| Model training/inference batch | `v2/forecasting/pipeline.py` + model modules |
| Persist forecasts | `v2/forecasting/forecast_store_io.py` |
| Persist order runs | `api/services/order_run_store.py` |
| Chatbot safety | `api/services/chatbot_tools.py` |

---

*Last updated for repo layout on `main` after Phases 1–3 + local POS sales wiring.*
