# Reorder AI

Backend API for Wecomm POS vendor reorder (W-1).  
**TL owns the UI.** This repo only exposes endpoints the UI will call.

---

## Product workflow (what TL’s UI does)

```text
TL UI
  1. User picks Vendor (dropdown)
  2. User sets Lead Time (L) and Days to Cover (C)
  3. UI calls our API
        │
        ▼
POST /api/detect-order
  { vendor_id | vendor_name, lead_time_days, time_to_cover_days }
        │
        ▼
Our service (per tenant Wecomm schema)
  • Load vendor catalog items          → products + product_vendor
  • Load current stock for those SKUs  → product_locations / stock tables
  • Forecast demand for window X=L+C   → forecast store / model
  • qty_to_order = need − available    (+ pack / expiry rules)
        │
        ▼
Response
  • full list of items to order
  • available stock, projected stock
  • justification + run_id
```

Vendor list for the dropdown:

```http
GET /api/detect-order
```

---

## How Wecomm tables fit (read path for detect-order)

| Need | Tenant tables (see `docs/`) |
|------|-----------------------------|
| Vendors | `vendors` |
| Catalog for vendor | `product_vendor`, `products`, `product_barcodes` |
| On-hand stock | `product_locations` (+ related stock/batch tables) |
| Sales history (model) | `orders`, `order_items` |
| Past receipts / invoices | `vendor_orders`, `vendor_order_products`, warehouse check-ins |
| Lead time hint | `product_vendor.lead_time_days` |

After the store **places** a PO in Wecomm (TL product, not this API), watch how these write:

| Action | Tables that change |
|--------|--------------------|
| Create vendor PO | `vendor_orders`, `vendor_order_products` |
| Receive goods | warehouse check-in / stock / `product_locations` qty up |
| Sell to customer | `orders`, `order_items`, stock down |

Full schema notes: `docs/REORDER_AI_DATABASE_ARCHITECTURE.md`

---

## What is in this repo (keep)

| Path | Why |
|------|-----|
| `api/routes/detect_order.py` | **Main TL endpoint** |
| `api/services/detect_order_service.py` | W-1 calculation + justification |
| `api/repositories/*` | DB + forecast reads (wire to live SQL next) |
| `api/routes/system.py` | `/api/health`, `/api/db-health` |
| `database/connectors/wecomm.py` | Azure Postgres connector |
| `config/`, `core/` | Settings + logging |
| `v2/inventory_math/` | Safety stock / ROP / pack math |
| `docs/` | DB architecture for Reorder AI |
| `tests/` | Unit tests for inventory math |

## What we removed (not for TL)

- CSV upload / train / Streamlit / store HTML UI  
- File-based analytics scripts and EDA  
- Duplicate `/api/vendor-order` stub  

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # set DB_* (SSH tunnel to Wecomm)
```

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

- Docs: http://localhost:8000/docs  
- DB: `GET /api/db-health` (tunnel must be up)

### Example detect-order call

```http
POST /api/detect-order
Content-Type: application/json

{
  "vendor_name": "OM",
  "lead_time_days": 5,
  "time_to_cover_days": 7
}
```

---

## Next build step

Wire `detect_order_repository` + `forecast_store` to **live Wecomm SQL** (tenant schema) instead of stubs, using sales → forecast → P90 for window `L+C`.
