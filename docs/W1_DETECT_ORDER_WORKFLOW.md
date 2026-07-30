# W-1 Detect Order — API contract for TL UI

## Responsibility split

| Owner | Responsibility |
|-------|----------------|
| **TL UI** | Vendor dropdown, lead time, days to cover, display order list |
| **This API** | Read Wecomm data + forecast → return items to order |
| **Wecomm POS** | Persist real POs / receipts / sales when store acts |

We do **not** ship storefront UI in this repo.

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

Response includes line items, stocks, justification, `run_id`.

### 3) Reload a run (chatbot / audit)

`GET /api/detect-order/runs/{run_id}`

## Data the model needs (all in Wecomm)

1. Vendor catalog — what this vendor sells to the store  
2. Current stock — on-hand for those SKUs  
3. Sales history — demand signal  
4. Past vendor orders / receipts — cadence / lead-time reality  
5. Pack / UOM — round order qty to shippable units  

## Observing workflow after a real order

When someone creates a vendor order **inside Wecomm UI** (not via our detect-order response alone):

1. Insert/update `vendor_orders`  
2. Insert lines in `vendor_order_products`  
3. On receive: stock / location / check-in tables increase  
4. On sell: `orders` / `order_items` + stock decrease  

Use that trail to validate that our recommendations match how the POS actually moves inventory.
