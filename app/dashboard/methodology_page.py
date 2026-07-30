"""Demo-friendly methodology page — formulas, forecasts, examples, weather."""

from __future__ import annotations

import streamlit as st


def render_methodology_page() -> None:
    st.title("How Everything Works")
    st.caption(
        "One place for all formulas, forecasts, lead-time rules, AI Min, "
        "negative stock, and weather — with clear examples."
    )

    tab_overview, tab_ai_min, tab_forecast, tab_lead, tab_neg, tab_weather, tab_flow = st.tabs(
        [
            "1. Big picture",
            "2. AI Min & formulas",
            "3. 7 / 14 / 30 forecasts",
            "4. With vs without lead time",
            "5. Negative stock",
            "6. Weather",
            "7. Full worked examples",
        ]
    )

    with tab_overview:
        _render_overview()
    with tab_ai_min:
        _render_ai_min()
    with tab_forecast:
        _render_forecast()
    with tab_lead:
        _render_lead_time()
    with tab_neg:
        _render_negative()
    with tab_weather:
        _render_weather()
    with tab_flow:
        _render_examples()


def _render_overview() -> None:
    st.markdown(
        """
### What this system does

For each product (SKU) we answer:

1. **How fast does it sell?** → Average Daily Sales (ADS)
2. **How much should we keep as a buffer?** → Safety stock
3. **When should we reorder?** → AI Min (reorder point)
4. **How many units will we sell soon?** → ML forecast (7 / 14 / 30 days)
5. **How many should we order now?** → Order qty (formula + forecast, rounded to pack)

---

### Two calculation paths (same math, slightly different stock handling)

| Path | Where | Negative stock |
|------|--------|----------------|
| **SKU Sales Analytics** (batch) | Offline analysis / Order Now list | Neg. count → **0** on-hand; \|neg\| added into ADS |
| **Vendor Reorder Planner** | Live vendor order screen | Same: on-hand **0**, \|neg\| into ADS |

---

### Simple flow

```
Sales history (30 days)
        ↓
   ADS + demand volatility
        ↓
Safety stock + Lead time  →  AI Min
        ↓
Compare to on-hand stock  →  Formula need
        ↓
ML forecast (7/14/30)     →  Forecast need
        ↓
max(formula, forecast) → round to case/pack → Order qty
```
"""
    )


def _render_ai_min() -> None:
    st.markdown("### Step-by-step formulas")

    st.markdown("#### Step 1 — Average Daily Sales (ADS)")
    st.latex(r"ADS = \frac{\text{total units sold in last 30 days}}{30}")
    st.info(
        "**Example:** Sold 60 units in last 30 days → ADS = 60 ÷ 30 = **2.0 units/day**"
    )

    st.markdown("#### Step 2 — Lead time (cover days)")
    st.markdown(
        """
- If the vendor has a **delivery schedule** → lead time = days from order cutoff to delivery  
- If **no schedule** → we use a default cover of **14 days**
"""
    )

    st.markdown("#### Step 3 — Safety stock")
    st.latex(r"Safety\ stock = Z \times \sigma_{daily} \times \sqrt{lead\ time}")
    st.markdown(
        """
**Why 1.65?**

| Service level | Z-score | Meaning |
|---------------|---------|---------|
| 90% | 1.28 | Stockouts ~10% of the time |
| **95% (we use this)** | **1.65** | Stockouts ~5% of the time |
| 99% | 2.33 | Stockouts ~1% of the time |

`1.65` is the standard normal Z-score for **95% service level** — we keep enough buffer so demand spikes during lead time are covered about 95% of the time.

- \\(\\sigma_{daily}\\) = standard deviation of daily sales in the last 30 days  
- If σ is missing → we approximate with `ADS × 0.3`  
- If ADS ≤ 0 → safety stock = **0**
"""
    )
    st.info(
        "**Example:** ADS = 2, σ = 1.2, lead = 7 days  \n"
        "SS = 1.65 × 1.2 × √7 ≈ 1.65 × 1.2 × 2.65 ≈ **5.2 → round to 5**"
    )

    st.markdown("#### Step 4 — AI Min (reorder point)")
    st.latex(r"AI\ Min = (ADS \times lead\ time) + Safety\ stock")
    st.markdown(
        """
**Meaning:** Minimum on-hand we want when we place an order — enough to cover sales during lead time **plus** a safety buffer.
"""
    )
    st.success(
        "**Example:** ADS = 2, lead = 7, SS = 5  \n"
        "AI Min = (2 × 7) + 5 = **19 units**  \n"
        "→ If on-hand ≤ 19, flag **Order Now**."
    )

    st.markdown("#### Step 5 — Formula need & pack rounding")
    st.latex(r"Formula\ need = \max(0,\ AI\ Min - stock)")
    st.latex(r"Order\ qty = \left\lceil\frac{need}{pack}\right\rceil \times pack")
    st.info(
        "**Example:** Need 15 units, pack = 12 → order **24** (2 cases), not 15."
    )

    st.markdown("#### EOQ (in code, not used for daily reorder)")
    st.latex(r"EOQ = \sqrt{\frac{2 \times D \times S}{H}}")
    st.caption("Economic Order Quantity exists in the codebase but is not used in the live reorder recommendation.")


def _render_forecast() -> None:
    st.markdown(
        """
### What the ML forecast is

We train **three separate LightGBM models** (falls back to XGBoost if needed):

| Column | What it predicts |
|--------|------------------|
| **forecast_7d** | Expected units sold in the **next 7 days** |
| **forecast_14d** | Expected units sold in the **next 14 days** |
| **forecast_30d** | Expected units sold in the **next 30 days** |

They are **not** the same model scaled by days. Each horizon has its own target and its own model.

---

### How training works (plain English)

1. Build a **SKU × day** table (every product, every day; days with no sale = 0).
2. For each day, create a target:
   - `target_7d` = sum of sales over the **next 7 days**
   - same idea for 14 and 30
3. Teach the model using features like:
   - recent sales lags (1, 7, 14, 28 days ago)
   - rolling averages (7 / 14 / 30)
   - calendar (weekend, festival, school break, payday)
   - promo / price
   - current stock, out-of-stock flag, vendor lead, AI Min, safety stock
4. At forecast time: take the **last day** of history for each SKU → predict 7d, 14d, 30d → clip at 0 (`max(pred, 0)`).

---

### How forecasts are used in reorder

| Use | Horizon |
|-----|---------|
| **Order Now** urgency | Compare **7-day** forecast to stock |
| **Order quantity** (SKU analysis) | Use **14-day** forecast need vs formula need → take the **max** |
| **Vendor planner** | Slider for cover days (1–60); ML maps to nearest 7 / 14 / 30 forecast |

```
Order Now  =  stock ≤ AI Min   OR   forecast_7d > stock

Order qty  =  round_to_pack( max( formula_need, forecast_14d_need ) )
```

---

### Why 7d can look “high” and 14d “low” (or the opposite)

- Separate models → they can disagree a little.
- Slow movers often show tiny numbers (0.05–0.2) that look like zero on screen.
- Vendor page often shows **one** forecast column based on cover days — not all three at once.
- About ~24% of SKUs can have `forecast_7d > forecast_14d` even though that is not ideal mathematically — independent models.

---

### Tiny numeric example

| Metric | Value |
|--------|-------|
| Stock on hand | 10 |
| AI Min | 19 |
| forecast_7d | 15 |
| forecast_14d | 28 |
| Pack | 12 |

- Formula need = max(0, 19 − 10) = **9**  
- ML need (14d) = max(0, 28 − 10) = **18**  
- Recommended raw = max(9, 18) = **18** → pack round → **24**  
- Order Now = Yes (10 ≤ 19, and also 15 > 10)
"""
    )


def _render_lead_time() -> None:
    st.markdown(
        """
### Where lead time comes from

| Vendor situation | What we use | Label in UI |
|------------------|-------------|-------------|
| **Has delivery schedule** (cutoff + delivery days known) | Estimated days from order day → delivery | Schedule mode |
| **No schedule / unknown** | Default **14-day cover** | Forecast mode |

Default constant in code: `DEFAULT_NO_SCHEDULE_COVER_DAYS = 14`.

On the Vendor Reorder Planner, for unscheduled vendors use the **lead-time slider** (1–60 days) to set pallet / delivery cover.

---

### Case A — Vendor HAS lead time / schedule

Example: lead time = **7 days**

1. ADS from last 30 days (say 2.0)
2. Safety stock = 1.65 × σ × √7
3. AI Min = (ADS × **7**) + SS
4. Formula need = AI Min − stock
5. ML: vendor planner uses **forecast_7d** (because cover ≤ 7)
6. Order = max(formula need, ML need), then pack-round

**Intuition:** Cover demand until the truck arrives.

---

### Case B — Vendor has NO lead time / no schedule

We do **not** invent a fake delivery day. We plan a **demand cover window**:

1. Cover days = **14** (default), or the user’s 7/14/21/30 choice
2. AI Min = (ADS × **14**) + SS using √14
3. Formula need = AI Min − stock
4. ML: use matching horizon:
   - ≤7 → forecast_7d  
   - ≤14 → forecast_14d  
   - ≤21 → blend of 14d and 30d  
   - else → forecast_30d
5. Order = max(formula, ML), pack-round

**Important:** Forecast models still run the same way for every SKU. Lead time does **not** change how forecasts are trained — it only changes:
- which cover window AI Min uses
- which forecast column the vendor screen picks for the order

---

### Side-by-side mini example (same product)

Assume ADS = 2, σ = 1.2, stock = 10, pack = 1  
forecast_7d = 15, forecast_14d = 28

| | With schedule (lead = 7) | No schedule (cover = 14) |
|--|--------------------------|---------------------------|
| Lead-time demand | 2 × 7 = 14 | 2 × 14 = 28 |
| Safety stock | 1.65×1.2×√7 ≈ 5 | 1.65×1.2×√14 ≈ 7 |
| **AI Min** | **19** | **35** |
| Formula need | 19−10 = 9 | 35−10 = 25 |
| ML need | 15−10 = 5 (uses 7d) | 28−10 = 18 (uses 14d) |
| **Order** | max(9,5) = **9** | max(25,18) = **25** |

Same product, different vendor schedule → different AI Min and order size.
"""
    )


def _render_negative() -> None:
    st.markdown(
        """
**Negative count:** POS may show **−45**. That usually means sold after a vendor receive wasn’t added to count (you may still hold some physical stock — we can’t know exact).

**Math rule:**
1. **On-hand for reorder need** → treat negative as **0** (don’t inflate order by |−45|)
2. **ADS** → add **|−45| = 45** as sold into the lookback total  

Example: sold 30d from POS = 10, stock = −45 → ADS uses **10 + 45 = 55** sold ÷ 30; need = AI min − **0**.

---

### Worked examples

**Shared inputs:** AI Min = 40, forecast_14d = 50, pack = 10

#### Example 1 — Positive stock (stock = 25)

| Path | Need math | Order (after pack) |
|------|-----------|--------------------|
| Batch | max(0,40−25)=15; ML max(0,50−25)=25 → **25** → **30** | 30 |
| Vendor | same idea with raw stock → **30** | 30 |

#### Example 2 — Zero stock (stock = 0)

| Path | Need | Order |
|------|------|-------|
| Both | formula 40, ML 50 → **50** → pack **50** | 50 |

#### Example 3 — Negative stock (stock = −100)

| Path | Need math | Order |
|------|-----------|-------|
| **Batch (SKU analysis)** | effective=0 → formula 40, ML 50 → **50** | **50** |
| **Vendor planner** | formula 40−(−100)=**140**, ML 50−(−100)=**150** → **150** | **150** |

Negative inventory is treated more aggressively on the **Vendor Reorder** screen so you catch up faster after overselling.
"""
    )


def _render_weather() -> None:
    st.markdown(
        """
### Where weather comes from

| Item | Detail |
|------|--------|
| **Provider** | [Open-Meteo](https://open-meteo.com/) (free weather API) |
| **Location** | Okemos, MI — lat **42.7223**, lon **−84.4274** (near East Lansing) |
| **Timezone** | `America/Detroit` |
| **History API** | `https://archive-api.open-meteo.com/v1/archive` |
| **Forecast API** | `https://api.open-meteo.com/v1/forecast` |
| **Local cache** | `data/cache/okemos_weather_2026.json` and `okemos_weather_forecast.json` |

---

### Attributes we pull (daily)

| API field | What we store / use | Meaning |
|-----------|---------------------|---------|
| `temperature_2m_max` | `temp_max_f` | Daily high (°C → °F) |
| `temperature_2m_min` | `temp_min_f` | Daily low (°C → °F) |
| `precipitation_sum` | `precip_in` | Rain/snow total (mm → inches) |
| `weathercode` | `weather_code` + `weather_label` | WMO code → Clear, Rain, Snow, Fog, Thunderstorm, etc. |

**Labels we map** (examples): Clear, Partly cloudy, Overcast, Fog, Drizzle, Rain, Heavy rain, Snow, Rain showers, Thunderstorm.

---

### How weather enters the math

1. **Feature / EDA** — joined onto sales by date for correlation and analysis.
2. **Calendar uplift only on orders** (weekend / festival / holiday) — EDA showed **strong** weekend correlation (~0.55). Weather correlation was **weak / near zero**, so weather is **not** used to inflate order qty.
3. Weather still appears in the **Future outlook** tab for context only.

Order uplift does **not** replace ADS or AI Min — and weather does **not** change order qty.
"""
    )


def _render_examples() -> None:
    st.markdown("### End-to-end examples")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown(
            """
#### Product A — Scheduled vendor (lead = 7)

| Input | Value |
|-------|-------|
| Sold last 30 days | 60 |
| ADS | 2.0 |
| Daily σ | 1.2 |
| Lead time | 7 |
| On hand | 8 |
| Pack | 12 |
| forecast_7d | 16 |
| forecast_14d | 30 |

**Calculate:**
1. SS = 1.65 × 1.2 × √7 ≈ **5**
2. AI Min = (2 × 7) + 5 = **19**
3. Formula need = 19 − 8 = **11**
4. ML need (14d in batch) = 30 − 8 = **22**
5. Raw = max(11, 22) = **22** → pack → **24**
6. Order Now? 8 ≤ 19 → **Yes** (also 16 > 8)
"""
        )
    with col2:
        st.markdown(
            """
#### Product B — No schedule (cover = 14)

| Input | Value |
|-------|-------|
| Same ADS / σ | 2.0 / 1.2 |
| Cover | **14** (default) |
| On hand | 8 |
| Pack | 12 |
| forecast_14d | 30 |

**Calculate:**
1. SS = 1.65 × 1.2 × √14 ≈ **7**
2. AI Min = (2 × 14) + 7 = **35**
3. Formula need = 35 − 8 = **27**
4. ML need = 30 − 8 = **22**
5. Raw = max(27, 22) = **27** → pack → **36**
6. Order Now? 8 ≤ 35 → **Yes**
"""
        )

    st.markdown("---")
    st.markdown(
        """
#### Product C — Negative stock (count = −50)

| Input | Value |
|-------|-------|
| POS sold lookback | (whatever from sales files) |
| Extra sold from count | **50** (\|−50\|) |
| On-hand for need | **0** |
| AI Min | (from ADS including +50) |
| Pack | 10 |

**Math:**
- ADS uses POS sold **+ 50**
- Need = AI Min − **0** (not AI Min − (−50))
- Physical may still hold some units (forgot receive) — we don’t invent that stock

---

### Cheat sheet

| Term | Formula / rule |
|------|----------------|
| ADS | 30-day units ÷ 30 |
| Z | **1.65** = 95% service level |
| Safety stock | Z × σ × √lead |
| AI Min | (ADS × lead) + SS |
| No schedule | lead/cover = **14** days |
| forecast_Nd | ML expected sales in next N days |
| Order Now | stock ≤ AI Min **or** forecast_7d > stock |
| Order qty | max(formula need, forecast need) → pack round |
| Weather | Open-Meteo, Okemos MI — temp max/min, precip, weather code |
"""
    )
