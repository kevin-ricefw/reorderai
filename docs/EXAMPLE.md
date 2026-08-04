# Reorder example — cover-C math (illustrative)

One SKU walkthrough with the **current** formulas (cover after arrival, full cases, ADS from sales only).

## Inputs

| Input | Value |
|--------|--------|
| Product | Example intermittent SKU |
| Pack | 4 units / case |
| Lead time **L** | 3 days |
| Days to cover **C** | 14 days |
| Planning window **X** | **L + C = 17 days** |
| On-hand | 5 units |
| Units sold last 90d | 32 |
| Selling days | 8 of 90 (rest zero-sale) |
| Demand class | intermittent |
| Uplift | 1.0 (no festival lift for this SKU) |

## Step-by-step

### 1) ADS (sales only)

```text
ADS = 32 / 90 = 0.3556 units/day
```

If sold units = 0 → ADS = 0 → **SKIP** (even if ML P50 is large).  
Never set ADS = P50 / horizon.

### 2) ADS × X (audit)

```text
ads_times_x = 0.3556 × 17 ≈ 6.04
```

### 3) Lead burn + ROP (urgency only)

```text
lead_demand = ADS × L = 0.3556 × 3 ≈ 1.07
SS(L)       ≈ Z × σ × √L
ROP         = lead_demand + SS(L)
stock_at_arrival = max(0, 5 − 1.07) ≈ 3.93
below_ROP   = (5 < ROP)   → depends on σ
```

### 4) Cover after arrival (sets desired stock)

```text
cover_demand = ADS × C = 0.3556 × 14 ≈ 4.98
SS(C)        ≈ Z × σ × √C
Desired      = ceil(ADS×C×uplift + SS(C))
             = ceil(4.98×1.0 + SS(C))
```

### 5) Order qty — full cases

```text
raw_need     = max(0, Desired − stock_at_arrival)
cases        = ceil(raw_need / pack)
qty_to_order = cases × pack
```

### 6) Action

| Condition | Action |
|-----------|--------|
| qty &gt; 0 | **ORDER** |
| below ROP but raw_need = 0 | **WATCH** |
| ADS≈0 or already covered | **SKIP** |

### 7) Festivals (next X days)

Calendar scanned from `as_of` (`REORDER_TZ=America/Detroit`).  
Listed in justification / `upcoming_festivals`.  
Raises Desired only if this SKU has a learned uplift for those tags.

### 8) ML reference (does not set qty)

P50/P90 for X are shown for comparison only.

---

## Dead-stock counterexample (why we fixed invent-from-P50)

| Field | Bad (old bug) | Good (current) |
|-------|----------------|----------------|
| On hand | 0 | 0 |
| Real sales / ADS | 0 | 0 |
| ML P50 for 14d | 148.4 | 148.4 |
| ADS used | **10.6 invented** (= P50/14) | **0** |
| Qty | **100 ORDER** | **0 SKIP** |
