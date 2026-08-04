# Reorder AI — Complete System Workflow (current)

End-to-end map of folders, tables, APIs, nightly job, and detect-order math.

---

## 1. Big picture

```text
┌─────────────────────────────────────────────────────────────────┐
│  A) DATA IN                                                     │
│     data/sales/*.csv + data/inventory/*COUNT*.csv               │
│     → scripts/import_local_to_paul.py --execute                 │
│     → tenant: ai_pos_daily_sales, product_locations,            │
│       products, product_barcodes, product_vendor, vendors       │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  B) NIGHTLY ML (Azure VM systemd @ 02:00 America/Detroit)       │
│     scripts/run_nightly_forecast.py                             │
│     classify → Croston / TSB / LightGBM / rules → P50/P90       │
│     + learn SKU weekend/festival uplift                         │
│     → overwrite data/forecast_store/                            │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  C) DETECT-ORDER API                                            │
│     vendor + L + C → X=L+C                                      │
│     ADS(90d sales only) + SS + ROP + cover-C desired            │
│     + festival scan + SKU uplift on ADS×C                       │
│     → full-case qty · ORDER/WATCH/SKIP · justification          │
│     → run_id JSON + Excel                                       │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  D) CLIENTS                                                     │
│     Streamlit blue UI · TL HTTP · chatbot tools (run-scoped)    │
└─────────────────────────────────────────────────────────────────┘
```

**Live base URL:** `http://74.249.36.238:8000`  
**Tenant example:** `wecomm_019fc887-24aa-70e9-b85b-7e969082193b`

---

## 2. Database tables

### Core

| Table | Contents | Role |
|-------|----------|------|
| `vendors` | id, name | Vendor picker |
| `products` | id, name, sku, pack (`min_reorder_quantity`) | Catalog + pack |
| `product_barcodes` | barcode ↔ product_id | UPC match |
| `product_vendor` | vendor ↔ product | Vendor catalog |
| `product_locations` | on-hand quantity, min/max | Stock |
| `ai_pos_daily_sales` | sale_date, product_id, upc, quantity | ADS + nightly demand |

### Supporting

| Table | Role |
|-------|------|
| `orders` / `order_items` | Fallback demand if AI sales empty |
| `vendor_orders` / `vendor_order_products` | Last pallet qty; catalog fallback |
| `product_batches` | Expiry capping |
| `warehouses` / `warehouse_locations` | Stock insert targets on import |
| `product_vendor_histories` | Cleared on full product_vendor refresh |
| `categories` | Optional category labels for pipeline |

### Local files (not Postgres)

| Path | Role |
|------|------|
| `data/sales/` | Daily Product Sales CSVs |
| `data/inventory/` | CURRENT INVENTORY COUNT.csv |
| `data/forecast_store/` | Nightly P50/P90 + SKU uplift |
| `data/cache/order_runs/` | Saved `run_id` JSON |

---

## 3. API endpoints

Mounted in `api/main.py`.

| Method | Path | Handler |
|--------|------|---------|
| GET | `/` | Endpoint index |
| GET | `/api/health` | `api/routes/system.py` |
| GET | `/api/db-health` | Postgres ping |
| GET | `/api/detect-order` | Vendor list or query-param run |
| POST | `/api/detect-order` | Main reorder |
| GET | `/api/detect-order/runs/{run_id}` | Load run |
| GET | `/api/detect-order/runs/{run_id}/export.xlsx` | Excel |
| GET | `/api/chatbot/tools` | Tool list |
| POST | `/api/chatbot/ask` | Natural language → fixed tool |
| POST | `/api/chatbot/tool` | Direct tool call |

### Detect-order call chain

```text
POST /api/detect-order
  api/routes/detect_order.py → detect_order_post
    api/services/detect_order_service.py → detect_order()
      DetectOrderRepository  → vendors, catalog, stock, batches, POs
      ForecastStore          → ADS (sales) + P50/P90 files + SKU uplift
      festival_calendar      → festivals in next X days
      reorder_engine.compute_line_reorder()
      _template_justification()   # no GPT
      order_run_store.save_order_run()
```

### Order math (`api/services/reorder_engine.py`)

```text
ROP      = ADS×L + SS(L)                 → urgency only
Desired  = ceil(ADS×C×uplift + SS(C))
Arrival  = max(0, OH − ADS×L)
qty      = round UP to full cases
ADS≈0    → SKIP
ads_times_x = ADS×X
P50/P90  → reference only
```

ADS is **never** invented from P50 (`forecast_store.py`).

---

## 4. Nightly forecast

| Piece | Location |
|-------|----------|
| Entry | `scripts/run_nightly_forecast.py` → `main()` |
| Demand load | `v2/forecasting/sales_loader.py` |
| Pipeline | `v2/forecasting/pipeline.py` → `run_forecast_pipeline()` |
| Models | croston / syntetos_boylan / smooth_lgbm / monte_carlo |
| Festivals | `v2/forecasting/festival_calendar.py` |
| SKU uplift learn | `v2/forecasting/sku_uplift.py` |
| Persist | `v2/forecasting/forecast_store_io.py` → `data/forecast_store/` |
| VM schedule | `deploy/reorder-nightly-forecast.timer` (02:00 America/Detroit) |
| Log | `/var/log/reorder-ai/nightly-forecast.log` |

---

## 5. Data import

`scripts/import_local_to_paul.py --execute`

1. Load inventory CSV → create missing products/barcodes  
2. Refresh vendors + `product_vendor`  
3. Full refresh `product_locations.quantity`  
4. Truncate+reload `ai_pos_daily_sales` from all sales CSVs  
5. Does **not** wipe live POS `orders` / `order_items`

---

## 6. Repo tree

```text
inventory-ai/
├── api/                 FastAPI routes, services, repositories, schemas
├── v2/forecasting/      Nightly ML + festivals + uplift
├── v2/inventory_math/   SS, ROP, pack
├── v2/invoices/         Last invoice helper
├── database/            Wecomm connector + tenant
├── config/              settings + data paths
├── scripts/             import, nightly, export
├── demo_app/            Streamlit UI (blue theme)
├── deploy/              systemd timer/service + TL handoff
├── docs/                This workflow + W1 + EXAMPLE + phases
├── data/                Local dumps (gitignored)
├── tests/               Unit + math contract tests
└── .streamlit/          Theme (blue primary)
```

---

## 7. Env (production VM highlights)

```
TENANT_SCHEMA=wecomm_…
DETECT_ORDER_USE_LIVE_SQL=1
FORECAST_STORE_USE_BATCH=1
FORECAST_STORE_USE_LIVE_SQL=1
FORECAST_USE_LOCAL_SALES=0
SKU_UPLIFT_ENABLED=1
ADS_LOOKBACK_DAYS=90
REORDER_TZ=America/Detroit
```

---

## 8. What we do not use

- GPT / OpenAI for justification (removed)
- Inventing ADS from ML P50
- 80% pack gate (always round **up** to full cases)
- Min-on-hand floor for order qty
