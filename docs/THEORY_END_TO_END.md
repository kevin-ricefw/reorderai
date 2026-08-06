# Reorder AI — End-to-End Theory (Forecast → Order Math)

This document is **theory only**: how demand is classified, how nightly forecasting works, what Monte Carlo / P50 / P90 mean **in this system**, and how order quantities are calculated. It also corrects a few common misreadings of the flow.

---

## Big picture (two clocks)

Think of the system as **two jobs** that must not be mixed:

| Job | When | Purpose |
|-----|------|---------|
| **Nightly forecast** | ~2:00 AM (America/Detroit) | Learn each SKU’s sales shape → write P50/P90 + uplift table to disk |
| **Detect-order** | When a buyer runs it | Use **live sales ADS** + classical safety stock + calendar uplift → ORDER / WATCH / SKIP |

**Ordering does not use P50/P90 as the order quantity.**  
P50/P90 are a **demand scenario view** (ML reference). The PO qty uses **ADS + SS + uplift**.

```text
SALES HISTORY
     │
     ▼
NIGHTLY JOB ──► classify SKU ──► pick model ──► (Monte Carlo or LightGBM)
     │                              │
     │                              └─► store P50/P90 for 7/14/21/30/45 days
     └─► learn weekend/festival uplift table
                │
                ▼
DETECT-ORDER (buyer) ──► ADS from last ~90 days of sales
                │
                ├─► ROP = ADS×L + SS(L)     → urgency only
                ├─► Desired = ceil(ADS×C×uplift + SS(C))
                ├─► Arrival stock = max(0, OH − ADS×L)
                └─► Qty = round up to full cases
                     P50/P90 shown beside it for comparison only
```

---

## Part 1 — Corrected understanding of your flow

### What you got right

1. Nightly job trains / refreshes forecasts from sales history.
2. Each product is put into a demand category (smooth / intermittent / …).
3. Smooth products can use LightGBM; sparse products use intermittent models.
4. Croston-style models are meant for items that **do not sell every day** (probability of a sale day + typical size when it sells).
5. Monte Carlo builds many possible futures and summarizes them as **P50** and **P90**.
6. ADS is “average units per day” over a lookback (often 90 days), including zero-sale days.
7. Calendar effects (weekends / festivals) can raise the cover target via an **uplift** multiplier.
8. ROP / cover math then decides whether to order and how much.

### What to correct

| Your idea | Reality in this codebase |
|-----------|---------------------------|
| Everyday sales → LightGBM; otherwise → Croston | Too coarse. We use **Syntetos–Boylan** (ADI + CV²) into **four** classes. Only **smooth → LightGBM**. **Intermittent → Croston-SBA**. **Erratic / lumpy → TSB** (not Croston). Thin history → simple rule. |
| Horizons 7 / 14 / 21 / **35** | Stored horizons are **7 / 14 / 21 / 30 / 45**. |
| Monte Carlo “pretends tomorrow happens again and again” | Close, but cleaner: for each simulated day it draws **(sale happens? yes/no)** and **(how many units if yes)**, sums over the horizon, repeats ~2000 times. |
| P50 = stock before pallet arrives; P90 = safe cover for X days | **Wrong.** Those are **demand percentiles**, not stock levels. Stock before arrival = `max(0, OH − ADS×L)`. Cover target = `ceil(ADS×C×uplift + SS(C))`. |
| Safety stock = P90 − P50 | **Wrong here.** SS is classical: `Z × σ × √days`. P90−P50 is **not** used for SS or qty. |
| ADS is always 180/90 = 2, then 20 × 1.35 = 27 | ADS is **that SKU’s** units ÷ lookback. Desired is `ceil(ADS×C×uplift + SS(C))`, not “plain ADS×uplift” alone. |
| Uplift is learned inside LightGBM with festival features | **No.** LightGBM only sees day-of-week + lags. Festival/weekend uplift is a **separate learned table** applied at order time. |

---

## Part 2 — Nightly job (training / forecasting)

### Inputs

- Daily demand per SKU (prefer local Product Sales CSVs, else DB sales).
- Optional lookback (`FORECAST_LOOKBACK_DAYS`; `0` = all history).
- Calendar used later for uplift learning (weekend / festival tags).

### Steps

1. **Load** daily units sold per SKU.
2. **Classify** each SKU (Syntetos–Boylan).
3. **Fit models** per class (LightGBM pool for smooth; Croston-SBA / TSB / rule for others).
4. For intermittent-style models, run **Monte Carlo** → P50 / P90 at each standard horizon.
5. **Learn SKU uplift table** (how much more this SKU sells on weekend/festival days vs midweek baseline).
6. **Write** `data/forecast_store/` (classifications, base P50/P90, uplift table).

Detect-order **reads** those files. It does **not** retrain when you click Generate Order.

---

## Part 3 — Demand classification (why not “every day / not every day”)

We measure two things on the daily series (zeros filled in):

### ADI — Average Demand Interval

Average gap **in days** between sale days.

```text
If sales on days 1, 4, 10:
gaps = 3, 6
ADI  = mean(3, 6) = 4.5
```

- Low ADI ≈ sells often (near every day).
- High ADI ≈ long quiet gaps between sales.

### CV² — size variability (nonzero days only)

```text
CV² = (std of nonzero sale sizes / mean of those sizes)²
```

- Low CV² ≈ when it sells, size is fairly stable.
- High CV² ≈ wild spikes (1 unit one day, 40 the next).

### Thresholds (Syntetos–Boylan)

```text
ADI threshold  = 1.32
CV² threshold  = 0.49
```

| ADI | CV² | Class | Model used for P50/P90 |
|-----|-----|-------|-------------------------|
| &lt; 1.32 | &lt; 0.49 | **Smooth** | Pooled **LightGBM** (fallback: daily bootstrap) |
| ≥ 1.32 | &lt; 0.49 | **Intermittent** | **Croston-SBA** → Monte Carlo |
| &lt; 1.32 | ≥ 0.49 | **Erratic** | **TSB** → Monte Carlo |
| ≥ 1.32 | ≥ 0.49 | **Lumpy** | **TSB** → Monte Carlo |
| 0–1 sale days in history | — | **single_demand_day** | Simple **rule** → Monte Carlo |

**Intuition**

- **Smooth:** almost daily, steady sizes → LightGBM can use lag patterns.
- **Intermittent:** long gaps, stable sizes when it sells → Croston (learns *how often* + *how much*).
- **Lumpy / erratic:** messy gaps and/or wild sizes → **TSB** (updates probability every day; better for bursty SKUs than classic Croston).
- **Not** “lumpy uses Croston.” Lumpy uses **TSB**.

---

## Part 4 — What each model is doing (theory)

### A) LightGBM (smooth only)

Builds daily features from history:

- day of week
- lag 1 (yesterday)
- lag 7 (same weekday last week)
- 7-day rolling mean

Then recursively predicts the next days and sums them:

```text
P50 ≈ sum of daily point forecasts over horizon h
P90 ≈ P50 + 1.28 × residual_std × √h   (uncertainty band)
```

No festival columns inside LightGBM. Calendar uplift is applied later at order time.

### B) Croston-SBA (intermittent)

Separates two ideas:

1. **Interval** between sales → demand probability `p ≈ 1 / interval`
2. **Size** when a sale happens → `z`

SBA adjusts the daily mean slightly so Croston does not over-forecast intermittent items.

Those fitted `p` and `z` feed Monte Carlo.

### C) TSB — Teunter–Syntetos–Babai (erratic + lumpy)

Like Croston, but probability is smoothed **every day** (including zero days), so it reacts better when demand comes in bursts. Again feeds Monte Carlo with `p` and size.

### D) Rule-based (very thin history)

Spreads sparse volume into a conservative occurrence probability so Monte Carlo still has something sane to simulate.

---

## Part 5 — Monte Carlo: real purpose

### Purpose

Answer: **“Over the next h days, what total demand looks like in a typical case vs a high case?”**

It is a **demand simulator**, not an inventory simulator. It does **not** model on-hand, pallets, or arrival stock.

### How it works (intermittent / TSB / rule path)

For each of ~**2000** simulations, for each day `t = 1…h`:

```text
1) Draw size_t   from historical nonzero sale sizes (bootstrap)
                 or from a mild lognormal if history is thin
2) Draw occur_t  = Bernoulli(p)   # sale day or not
3) day_demand_t  = size_t × occur_t
4) path_total    = sum(day_demand_t over h days)
```

Then:

```text
P50 = 50th percentile of the 2000 path totals
P90 = 90th percentile of the 2000 path totals
```

### Simple picture

Imagine one intermittent SKU with:

- chance of a sale on any given day ≈ 10% (`p = 0.10`)
- when it sells, typical size ≈ 5 units

For a 14-day horizon, one simulation might look like:

```text
Day:  1  2  3  4  5  6  7  8  9 10 11 12 13 14
Sale: 0  0  5  0  0  0  0  5  0  0  0  0  0  0   → total = 10
```

Another simulation might total 0, another 25, etc. After 2000 runs you get a cloud of totals; P50 is the middle of that cloud, P90 is the high side (only 10% of sims go higher).

### What Monte Carlo is *not*

- Not “replay yesterday forever.”
- Not “stock before the truck arrives.”
- Not the formula that sets PO quantity in detect-order.

---

## Part 6 — What P50 and P90 really mean here

### Correct meaning

For a stored horizon (e.g. 14 days):

| Symbol | Meaning in Reorder AI |
|--------|------------------------|
| **P50** | Median expected **total units demanded** over those days (“typical demand scenario”) |
| **P90** | High-side **total units demanded** over those days (about 9 out of 10 sims are at or below this) |

At detect-order time they are **scaled linearly** to the buyer’s window X:

```text
P50(X) ≈ P50(from_h) × (X / from_h)
P90(X) ≈ P90(from_h) × (X / from_h)
```

They are shown next to the order line so a buyer can sanity-check:

> “AI wants ~24 cases from ADS math; ML median demand over X is ~18 and high-side ~31 — am I in the right ballpark?”

### Incorrect meanings (do not use)

| Wrong reading | Why wrong |
|---------------|-----------|
| P50 = stock that remains before pallet arrives | Arrival stock is `max(0, OH − ADS×L)` |
| P90 = safe cover stock for X days | Cover target is `ceil(ADS×C×uplift + SS(C))` |
| Order qty = P90 − on hand | Old idea; **not** current engine |
| SS = P90 − P50 | SS uses sales σ and service level Z, not ML percentiles |

### Why keep P50/P90 if they don’t set qty?

Because classification + intermittent models are still valuable:

- They explain **why** a SKU is lumpy vs smooth.
- They give a **demand scenario** (typical vs high) without forcing bad ADS invention.
- They protect us from an earlier bug: inventing ADS from P50 for dead stock (ADS=0 but ML still hummed) → false ORDER.

**Rule locked in code:** if real ADS ≈ 0 → **SKIP**, even if P50 looks large.

---

## Part 7 — Order math (what actually decides the PO)

Buyer inputs:

- **L** = lead time (days until delivery)
- **C** = cover days after arrival (how long you want stock to last once it lands)
- **X** = L + C (planning window used for festivals / display)

From sales (not from ML):

```text
ADS = (units sold in lookback) / lookback_days
      default lookback = 90
```

Zeros **count**. If 45 of 90 days are zero, ADS is pulled down. That is correct for “average per calendar day.”

Example:

```text
Sold 180 units in 90 days → ADS = 180/90 = 2.0 / day
Sold  32 units in 90 days → ADS = 32/90 ≈ 0.356 / day
Sold   0 units in 90 days → ADS = 0 → SKIP
```

### Safety stock (classical, not Monte Carlo)

```text
SS(days) = Z × σ × √days
```

- **Z** ≈ 1.65 for ~95% service level
- **σ** = std of selling-day quantities (with floors/caps so it doesn’t explode)
- Used twice: **SS(L)** for ROP urgency, **SS(C)** for cover buffer

### Core formulas

```text
ROP              = ADS×L + SS(L)                 # trigger / urgency only
lead_burn        = ADS×L
stock_at_arrival = max(0, on_hand − ADS×L)

Desired (AI tgt) = ceil(ADS × C × uplift + SS(C))
ADS cover (no uplift) = ceil(ADS × C + SS(C))

raw_need = max(0, Desired − stock_at_arrival)
qty      = ceil(raw_need / pack) × pack          # full cases only
```

| Piece | Job |
|-------|-----|
| ROP | “Are we in danger before the truck arrives?” → urgency / WATCH |
| Desired | “How much do we want on the shelf after arrival for C days?” |
| Arrival stock | What we expect to still have when the pallet lands |
| Qty | Gap to Desired, rounded **up** to cases |

Uplift multiplies **ADS×C only**, not SS:

```text
uplifted_sales = ADS × C × uplift
Desired        = ceil(uplifted_sales + SS(C))
```

---

## Part 8 — Worked example (your “2 × 10 × 1.35” intuition, completed)

### Inputs

| Input | Value |
|-------|--------|
| Units sold last 90d | 180 |
| ADS | 180/90 = **2.0** |
| L | 3 days |
| C | 10 days |
| X | 13 days |
| On-hand | 8 |
| Pack | 4 |
| σ (demand std) | 1.2 |
| Z | 1.65 |
| Uplift (festivals/weekends in next X) | **1.35** |

### Step A — urgency (ROP)

```text
SS(L) = 1.65 × 1.2 × √3 ≈ 1.65 × 1.2 × 1.732 ≈ 3.43
ROP   = 2.0×3 + 3.43 = 6 + 3.43 = 9.43

on_hand 8 < ROP 9.43 → below ROP (urgency raised)
lead_burn = 2×3 = 6
stock_at_arrival = max(0, 8 − 6) = 2
```

### Step B — cover target (this sets Desired)

Plain intuition you had:

```text
ADS × C = 2 × 10 = 20
```

With uplift + SS (what the system actually does):

```text
SS(C)            = 1.65 × 1.2 × √10 ≈ 1.65 × 1.2 × 3.162 ≈ 6.26
uplifted sales   = 20 × 1.35 = 27
Desired          = ceil(27 + 6.26) = ceil(33.26) = 34
```

So **not** “order 27.” Uplift raises expected sales; SS adds buffer; then ceil.

### Step C — order qty

```text
raw_need = max(0, 34 − 2) = 32
cases    = ceil(32 / 4) = 8
qty      = 8 × 4 = 32     → ORDER
```

### Side panel (ML reference only)

Suppose nightly Monte Carlo stored P50(14)=24, P90(14)=41. Scaled to X=13:

```text
P50(13) ≈ 24 × (13/14) ≈ 22.3
P90(13) ≈ 41 × (13/14) ≈ 38.1
```

Buyer sees: ADS math wants **32** units; ML typical ~**22**, high ~**38**. Useful context — **does not replace** the 32.

---

## Part 9 — Where uplift comes from

Uplift is **not** “ADS was never really 2 because some days were 5/7/1.”

ADS **already** averages those days (including zeros). Uplift answers a different question:

> Over the **upcoming** cover window, do this SKU’s **weekend / festival** days historically sell more than a midweek baseline?

Nightly learning (simplified):

```text
ratio = mean(sales on tagged special days) / mean(sales on Tue–Thu baseline)
keep if ratio ≥ ~1.08; cap ~1.75
```

At order time: look at each day in the next **X** days; take the strongest applicable SKU multiplier → `uplift`.

Example reading:

- Next 13 days include a long weekend this SKU usually spikes → uplift 1.35
- Midweek-only window with no festival tag for this SKU → uplift 1.0

---

## Part 10 — End-to-end story in one page

1. **History arrives** (POS / Product Sales CSVs).
2. **Nightly:** classify each SKU with ADI/CV².
3. **Smooth** → LightGBM daily path → horizon P50/P90.  
   **Intermittent** → Croston-SBA → Monte Carlo → P50/P90.  
   **Erratic / lumpy** → TSB → Monte Carlo → P50/P90.
4. **Nightly also** learns per-SKU weekend/festival uplift.
5. **Buyer runs detect-order** with L and C.
6. System computes **ADS from real sales** (zeros included).
7. **ROP** flags urgency; **Desired** sizes cover after arrival with uplift + SS.
8. **Qty** = gap to Desired, rounded up to cases → ORDER / WATCH / SKIP.
9. **P50/P90** shown as demand scenarios so humans can challenge the number — they do **not** invent ADS and do **not** set SS.

---

## Quick glossary

| Term | Meaning |
|------|---------|
| ADS | Average daily sales = units in lookback ÷ lookback days |
| ADI | Average gap between sale days |
| CV² | Variability of nonzero sale sizes |
| Croston-SBA | Intermittent model: sale frequency + size |
| TSB | Better intermittent model for bursty / lumpy SKUs |
| Monte Carlo | Many random demand futures → percentile totals |
| P50 / P90 | Median / high-side **demand over a horizon** |
| SS | Classical safety stock `Z×σ×√days` |
| ROP | Reorder-point trigger = ADS×L + SS(L) |
| C | Days of cover wanted after delivery |
| Uplift | Calendar multiplier on ADS×C only |
| SKIP | No order (often ADS≈0 / already covered) |

---

## Related docs

- Worked numeric example: [`EXAMPLE.md`](EXAMPLE.md)
- Detect-order steps: [`W1_DETECT_ORDER_WORKFLOW.md`](W1_DETECT_ORDER_WORKFLOW.md)
- System map: [`COMPLETE_SYSTEM_WORKFLOW.md`](COMPLETE_SYSTEM_WORKFLOW.md)
- Phase status: [`PHASE1_FORECASTING.md`](PHASE1_FORECASTING.md)
)
