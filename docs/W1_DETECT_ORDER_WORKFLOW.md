# Detect Order — API workflow

## Responsibility split

| Owner | Responsibility |
|-------|----------------|
| **UI** | Vendor dropdown, lead time, days to cover, display order list |
| **This API** | Read Wecomm data + forecast → return items to order |
| **Wecomm POS** | Persist real POs / receipts / sales when store acts |

No storefront UI in this repo.

## Endpoints

### 1) Populate vendor dropdown

`GET /api/detect-order`

Returns vendor list from DB (`vendors`).

### 2) Compute order for window

`POST /api/detect-order`

```json
{
  "vendor_id": "…",
  "vendor_name": "…",
  "lead_time_days": 5,
  "time_to_cover_days": 7
}
```

**Window:** `X = lead_time_days + time_to_cover_days`

**Per SKU (simplified):**

```text
projected_need  = P90_forecast(X)   # (+ caps / pack rules)
qty_to_order    = max(0, projected_need − available_stock)
```

### 3) Fetch saved run

`GET /api/detect-order/runs/{run_id}`

## Forecast source

Nightly batch writes P50/P90 into `data/forecast_store/`. Detect-order **reads** those files (no live model call per request).
