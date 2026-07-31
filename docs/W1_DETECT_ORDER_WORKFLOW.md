# Detect Order — precise workflow

Worked numbers: see [`EXAMPLE.md`](EXAMPLE.md) (AASHIRVAAD ATTA 10 LB, L=3, C=14).

## Inputs (every request)

| Input | Meaning |
|--------|---------|
| `vendor_id` / `vendor_name` | Which supplier catalog to order from |
| `lead_time_days` (**L**) | Days until the truck arrives (stock keeps selling) |
| `time_to_cover_days` (**C**) | Days of stock wanted **after** arrival |
| Window **X** | **X = L + C** only — no extra days are added |

## What the API reads

| Source | Use |
|--------|-----|
| `product_vendor` + `products` | Vendor catalog SKUs |
| `product_locations` | On-hand (negative treated as 0) |
| `ai_pos_daily_sales` / local sales | **ADS** + demand std (**90-day** lookback by default) |
| `data/forecast_store/` | Nightly ML **P50 / P90** (scaled to X) |
| SKU uplift table | Weekend / festival multiplier on P50/P90 |
| Past invoices / vendor POs | `last_pallet_qty` reference only (not order math) |

## Per-SKU math (exact order)

```text
1. ADS          = units_sold_last_90d / 90
2. lead_demand  = ADS × L
3. cover_demand = ADS × C
4. SS_lead      = Z × σ × √L          (Z≈1.65 @ 95%)
5. ROP          = ADS × L + SS_lead   → below_ROP = (on_hand < ROP)
6. ads_cover    = ADS × X + SS_X      (SS_X uses √X)
7. P50_X, P90_X = batch forecast for X  (× uplift if any)
8. AI_target    = max(P90_X, ads_cover)
9. raw_need     = max(0, AI_target − on_hand)
10. qty_to_order = pack-round(raw_need)
                 (full case only if raw_need ≥ 80% of pack)
```

**Important:** `below_reorder_point` is a **lead-time flag**.  
Order quantity is driven by **AI_target for X = L+C**, not by ROP alone.

## Endpoints

| Call | Purpose |
|------|---------|
| `GET /api/detect-order` | Vendor list |
| `POST /api/detect-order` | Build order for vendor + L + C |
| `GET /api/detect-order/runs/{run_id}` | Saved run JSON |
| `GET /api/detect-order/runs/{run_id}/export.xlsx` | Excel order sheet |

## Demo UI

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8001
python -m streamlit run demo_app/streamlit_app.py --server.port 8501
# or: run_demo.bat  (expects API on 8001)
```

## Forecast batch (must be current)

```bash
python scripts/run_nightly_forecast.py --lookback-days 0
```

Writes classifications + P50/P90 + SKU uplift into `data/forecast_store/`.  
Detect-order **reads** those files; it does not retrain per click.
