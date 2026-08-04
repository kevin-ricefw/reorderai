# Detect Order — precise workflow (current)

Worked numbers: [`EXAMPLE.md`](EXAMPLE.md).

## Inputs (every request)

| Input | Meaning |
|--------|---------|
| `vendor_id` / `vendor_name` | Which supplier catalog to order from |
| `lead_time_days` (**L**) | Days until the truck arrives (stock keeps selling) |
| `time_to_cover_days` (**C**) | Days of stock wanted **after** arrival |
| Window **X** | **X = L + C** only |
| `include_zero_orders` | If false (default): return ORDER + WATCH only |

## What the API reads

| Source | Use |
|--------|-----|
| `vendors` | Vendor list / detect |
| `product_vendor` + `products` + `product_barcodes` | Vendor catalog SKUs + pack |
| `product_locations` | On-hand (negatives → 0 for ordering; oversell counted into ADS) |
| `ai_pos_daily_sales` / local sales | **ADS** + demand std (**90-day** lookback) — **never invent ADS from P50** |
| `data/forecast_store/` | Nightly ML **P50 / P90** (reference only) |
| SKU uplift table | Weekend / festival multiplier on **cover sales** (ADS×C) at order time |
| Festival calendar (`festival_calendar.py`) | Tags in next X days (`as_of` = `REORDER_TZ`, default America/Detroit) |
| `product_batches` | Expiry cap when remaining shelf life &lt; X |
| Past invoices / `vendor_order_products` | `last_pallet_qty` reference only |

## Per-SKU math (exact order)

```text
1. ADS            = units_sold_last_90d / 90
                  (0 if no sales — do NOT use P50/horizon as ADS)
2. ads_times_x    = ADS × X                         (audit column)
3. lead_demand    = ADS × L
4. SS(L)          = Z × σ × √L                      (Z≈1.65 @ 95%)
5. ROP            = ADS × L + SS(L)
                  below_ROP = (on_hand < ROP)       → urgency / WATCH
6. stock_at_arrival = max(0, on_hand − ADS×L)
7. cover_eff      ≈ C  (or effective_days − L if expiry-capped)
8. SS(C)          = Z × σ × √cover_eff
9. Desired        = ceil(ADS × cover_eff × uplift + SS(C))
10. raw_need      = max(0, Desired − stock_at_arrival)
11. qty_to_order  = ceil(raw_need / pack) × pack    (full cases only)
12. If ADS≈0      → SKIP, qty=0 (dead stock)
13. line_action   = ORDER | WATCH | SKIP
14. justification = report-style template from outputs (no GPT)
```

**Important**
- ROP does **not** set order qty; cover after arrival does.
- ML P50/P90 are shown for comparison only.
- Festival uplift multiplies **cover sales**, not SS.

## Endpoints

| Call | Purpose |
|------|---------|
| `GET /api/detect-order` | Vendor list |
| `POST /api/detect-order` | Build order for vendor + L + C |
| `GET /api/detect-order/runs/{run_id}` | Saved run JSON |
| `GET /api/detect-order/runs/{run_id}/export.xlsx` | Excel order sheet |

## Demo UI

```bash
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
python -m streamlit run demo_app/streamlit_app.py --server.port 8501
# Default API base in UI: http://74.249.36.238:8000
```

## Forecast batch (must be current)

```bash
python scripts/run_nightly_forecast.py --lookback-days 0
```

On Azure VM: **systemd timer** `reorder-nightly-forecast.timer` at **02:00 America/Detroit**.  
Writes classifications + P50/P90 + SKU uplift into `data/forecast_store/` (overwrites previous).  
Detect-order **reads** those files; it does not retrain per click.
