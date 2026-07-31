# Reorder example — AASHIRVAAD ATTA 10 LB

Worked example for **one SKU** so the field meanings and formulas are clear.

## Inputs

| Input | Value |
|--------|--------|
| Product | AASHIRVAAD ATTA 10 LB |
| UPC | 841905080021 |
| Vendor catalog pack | 4 units / case |
| Lead time **L** | 3 days (truck wait) |
| Days to cover **C** | 14 days (stock after arrival) |
| Planning window **X** | **L + C = 17 days** |
| On-hand | 5 units |
| Demand class | intermittent |
| Last invoice qty (reference) | 8 |

ADS lookback = **90 days** (`ADS_LOOKBACK_DAYS`).

---

## Step-by-step numbers

### 1) ADS (average daily sales)

```text
ADS = (units sold in last 90 days) / 90
    = 0.3556 units/day
```

### 2) Lead demand (sells before truck arrives)

```text
lead_demand_ads = ADS × L
                = 0.3556 × 3
                = 1.0667 units
```

### 3) Cover demand (wanted after arrival)

```text
cover_demand_ads = ADS × C
                 = 0.3556 × 14
                 = 4.9778 units
```

### 4) Safety stock and reorder point (lead only)

```text
SS  = Z × σ × √L          (service level ~95%, Z ≈ 1.65)
    = 1.61

ROP = ADS × L + SS
    = 1.0667 + 1.61
    = 2.68
```

**Below reorder point?**  
`on-hand < ROP` → `5 < 2.68` → **FALSE**.

ROP is only a **lead-time urgency flag**. It does **not** decide whether to order for the full cover window.

### 5) Classic full-window need (ADS cover)

Safety for the full window X:

```text
SS_X = Z × σ × √X
ads_cover_qty = ADS × X + SS_X
              = 0.3556 × 17 + SS_X
              = 9.88
```

### 6) ML demand for X (with uplift)

Nightly batch forecast for ~17 days (nearest stored horizon, scaled to X):

| Field | Value |
|--------|--------|
| P50 | 4.86 |
| P90 | 7.29 |
| Uplift multiplier | 1.0 (no weekend/festival lift this window) |

### 7) AI target (order-up-to)

```text
AI target = max(P90 × uplift, ads_cover_qty)
          = max(7.29, 9.88)
          = 9.88
```

ADS cover won over P90 for this SKU.

### 8) Quantity to order

```text
raw need     = max(0, AI target − on-hand)
             = max(0, 9.88 − 5)
             = 4.88

pack         = 4
case rule    = recommend a case only if raw need ≥ 80% of pack
             → 4.88 ≥ 0.80 × 4 → yes → 1 case

qty_to_order = 4
cases        = 1
```

---

## Why order if Below ROP is FALSE?

| Question | Answer for this SKU |
|----------|---------------------|
| Will stock run out during **3 lead days**? | No — on-hand 5 > ROP 2.68 → `below_reorder_point = FALSE` |
| Is on-hand enough for **17 days (L+C)**? | No — need ~9.88, have 5 → **order 4** |

So the line is recommended because of the **full cover window**, not because it failed the ROP check.

```text
Below ROP  → urgency for lead time only
Order qty  → fill up to AI target for X = L + C
```

---

## Field cheat-sheet (same row as Excel / API)

| Field | Example value |
|--------|----------------|
| available_stock | 5 |
| ads | 0.3556 |
| lead_demand_ads | 1.0667 |
| cover_demand_ads | 4.9778 |
| safety_stock | 1.61 |
| reorder_point | 2.68 |
| below_reorder_point | FALSE |
| ads_cover_qty | 9.88 |
| uplift_multiplier | 1 |
| p50_demand | 4.86 |
| p90_demand | 7.29 |
| ai_target_qty | 9.88 |
| qty_to_order | 4 |
| cases_to_order | 1 |
| box_qty | 4 |
| horizon_days (X) | 17 |
| demand_class | intermittent |

Justification text matches this math: order 4 units (1 case × pack 4) for window L3+C14.
