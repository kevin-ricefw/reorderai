"""Future demand uplift from calendar, festivals, holidays, and weather."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from v2.forecasting.calendar_enrichment import enrich_dates, merge_calendar_features
from v2.forecasting.weather_enrichment import load_okemos_weather_forecast

COVER_DAY_OPTIONS: tuple[int, ...] = (7, 14, 21, 30)

# Per-SKU uplift clamps — soft bump only; never treat as "will sell one more case"
_SKU_UPLIFT_MIN_WEEKDAY_DAYS = 5
_SKU_UPLIFT_MIN_SIGNAL_DAYS = 5
_SKU_UPLIFT_MIN_LONG_WEEKEND_DAYS = 8
_SKU_UPLIFT_MAX = 1.35  # soft ceiling — past pattern ≠ guaranteed extra case


def learn_sku_uplift_factors(sku_daily: pd.DataFrame) -> dict[str, float]:
    """
    Learn calendar uplift for ONE product from its own sales history.

    Baseline = that SKU's average weekday daily sales (zeros included for missing days).
    Weekend / festival factors = that SKU's avg on those days ÷ weekday baseline.

    So snacks that spike Sat/Sun get >1.0; steady rice bags stay ~1.0.
    Factors are intentionally soft — history is a hint, not a sales guarantee.
    """
    empty = {"baseline": 1.0}
    if sku_daily is None or sku_daily.empty or "date" not in sku_daily.columns:
        return empty

    daily = sku_daily.copy()
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date"])
    if daily.empty:
        return empty
    if "quantity" not in daily.columns:
        return empty

    daily = daily.groupby("date", as_index=False)["quantity"].sum()
    daily["date"] = pd.to_datetime(daily["date"]).dt.normalize()
    # Fill zero-sale days so weekend/long-weekend means are not inflated by
    # "only count days it sold".
    start = daily["date"].min()
    end = daily["date"].max()
    if pd.notna(start) and pd.notna(end) and end > start:
        full_idx = pd.date_range(start, end, freq="D")
        daily = (
            daily.set_index("date")
            .reindex(full_idx, fill_value=0.0)
            .rename_axis("date")
            .reset_index()
        )
    daily = merge_calendar_features(daily, "date")
    if daily.empty:
        return empty

    weekday = daily[daily["day_type"] == "Weekday"]["quantity"]
    if len(weekday) < _SKU_UPLIFT_MIN_WEEKDAY_DAYS:
        baseline = float(daily["quantity"].mean() or 0.0)
    else:
        baseline = float(weekday.mean() or 0.0)
    if baseline <= 0:
        return empty

    factors: dict[str, float] = {"baseline": 1.0}

    def _ratio(mask: pd.Series, key: str, min_days: int = _SKU_UPLIFT_MIN_SIGNAL_DAYS) -> None:
        sub = daily.loc[mask, "quantity"]
        if len(sub) < min_days:
            return
        mean = float(sub.mean() or 0.0)
        if mean <= 0:
            return
        factors[key] = round(min(max(mean / baseline, 0.5), _SKU_UPLIFT_MAX), 3)

    for day_type in daily["day_type"].dropna().unique():
        _ratio(daily["day_type"] == day_type, f"day_type:{day_type}")

    _ratio(daily["indian_festival"].notna(), "indian_festival")
    _ratio(daily["us_holiday"].notna(), "us_holiday")
    if "is_long_weekend" in daily.columns:
        _ratio(
            daily["is_long_weekend"].astype(bool),
            "long_weekend",
            min_days=_SKU_UPLIFT_MIN_LONG_WEEKEND_DAYS,
        )

    return factors


def explain_sku_uplift_for_cover_window(
    sku_daily: pd.DataFrame,
    cover_days: int,
    *,
    from_date: date | None = None,
) -> dict:
    """
    Per-item uplift for the planning window using that SKU's own Sat/Sun/festival pattern.

    Only boosts when this product historically sells more on those day types.
    Never applies a store-wide factor (avoids boosting rice because samosas spike).
    """
    empty = {
        "uplift_factor": 1.0,
        "driver_date": None,
        "driver_day_name": None,
        "driver_day_type": None,
        "driver_festival": None,
        "driver_holiday": None,
        "driver_long_weekend": False,
        "window_days": int(cover_days),
        "formula": "uplifted_need = need × 1.0 (no per-item calendar uplift)",
        "summary": "No per-item uplift (this SKU does not sell more on upcoming weekend/festival days).",
        "factors_note": (
            "Per-SKU factors: this product's avg sales on Weekend/Festival/Holiday "
            "÷ this product's weekday avg. Store-wide boost is NOT used."
        ),
        "sku_factors": {"baseline": 1.0},
    }
    factors = learn_sku_uplift_factors(sku_daily)
    empty["sku_factors"] = factors
    if not factors or set(factors.keys()) <= {"baseline"}:
        return empty

    start = from_date or date.today()
    future_dates = [start + timedelta(days=i) for i in range(max(int(cover_days), 1))]
    cal = enrich_dates(future_dates, year=start.year)
    if cal.empty:
        return empty

    best_factor = 1.0
    best_row = None
    for _, row in cal.iterrows():
        uplift = float(_uplift_for_calendar_row(row, factors))
        # Only boost — never reduce order qty via "uplift"
        uplift = max(uplift, 1.0)
        if uplift > best_factor:
            best_factor = uplift
            best_row = row

    if best_row is None or best_factor <= 1.0:
        return empty

    fest = best_row.get("indian_festival")
    hol = best_row.get("us_holiday")
    day_type = best_row.get("day_type") or "Weekday"
    day_name = best_row.get("day_name") or ""
    long_wk = bool(best_row.get("is_long_weekend"))
    driver_date = best_row.get("date")
    if hasattr(driver_date, "date"):
        driver_date = driver_date.date()
    elif driver_date is not None:
        driver_date = pd.Timestamp(driver_date).date()

    drivers = [str(day_type)]
    if pd.notna(fest) and str(fest).strip():
        drivers.append(f"festival:{fest}")
    if pd.notna(hol) and str(hol).strip():
        drivers.append(f"holiday:{hol}")
    if long_wk:
        drivers.append("long weekend")

    factor = round(min(best_factor, _SKU_UPLIFT_MAX), 3)
    summary = (
        f"Per-item uplift = {factor:.3f} from this product's own history "
        f"(driver {driver_date} / {day_name}, {', '.join(drivers)}). "
        f"Not a store-wide boost."
    )
    formula = (
        f"sku_uplift = max(this SKU's calendar factors over next {int(cover_days)} days) "
        f"= {factor:.3f} (driver: {driver_date} / {day_name})"
    )
    return {
        "uplift_factor": factor,
        "driver_date": str(driver_date) if driver_date else None,
        "driver_day_name": str(day_name) if day_name else None,
        "driver_day_type": str(day_type),
        "driver_festival": str(fest) if pd.notna(fest) and str(fest).strip() else None,
        "driver_holiday": str(hol) if pd.notna(hol) and str(hol).strip() else None,
        "driver_long_weekend": long_wk,
        "window_days": int(cover_days),
        "formula": formula,
        "summary": summary,
        "factors_note": empty["factors_note"],
        "sku_factors": factors,
    }



def _learn_uplift_factors(enriched_daily: pd.DataFrame) -> dict[str, float]:
    """Learn average uplift vs baseline for day types, festivals, holidays, weather."""
    if enriched_daily.empty:
        return {"baseline": 1.0}

    daily = enriched_daily.groupby("date", as_index=False)["quantity"].sum()
    daily = merge_calendar_features(daily, "date")
    baseline = daily["quantity"].mean()
    if baseline <= 0:
        return {"baseline": 1.0}

    factors: dict[str, float] = {"baseline": 1.0}
    for day_type in daily["day_type"].dropna().unique():
        sub = daily[daily["day_type"] == day_type]["quantity"].mean()
        factors[f"day_type:{day_type}"] = round(sub / baseline, 3) if baseline else 1.0

    fest = daily[daily["indian_festival"].notna()]["quantity"].mean()
    if pd.notna(fest) and fest > 0:
        factors["indian_festival"] = round(fest / baseline, 3)

    us_hol = daily[daily["us_holiday"].notna()]["quantity"].mean()
    if pd.notna(us_hol) and us_hol > 0:
        factors["us_holiday"] = round(us_hol / baseline, 3)

    long_wk = daily[daily["is_long_weekend"]]["quantity"].mean()
    if pd.notna(long_wk) and long_wk > 0:
        factors["long_weekend"] = round(long_wk / baseline, 3)

    if "weather_label" in enriched_daily.columns:
        wdaily = enriched_daily.groupby(["date", "weather_label"], as_index=False)["quantity"].sum()
        for label in wdaily["weather_label"].dropna().unique():
            sub = wdaily[wdaily["weather_label"] == label]["quantity"].mean()
            if pd.notna(sub) and sub > 0:
                factors[f"weather:{label}"] = round(sub / baseline, 3)

    return factors


def _uplift_for_calendar_row(row: pd.Series, factors: dict[str, float]) -> float:
    uplift = factors.get("baseline", 1.0)
    day_type = row.get("day_type", "Weekday")
    uplift = factors.get(f"day_type:{day_type}", uplift)
    if pd.notna(row.get("indian_festival")):
        uplift = max(uplift, factors.get("indian_festival", uplift))
    if pd.notna(row.get("us_holiday")):
        uplift = max(uplift, factors.get("us_holiday", uplift))
    if row.get("is_long_weekend"):
        uplift = max(uplift, factors.get("long_weekend", uplift))
    return round(uplift, 3)


def forecast_calendar_uplift(
    enriched_daily: pd.DataFrame,
    *,
    horizon_days: int = 14,
    from_date: date | None = None,
) -> pd.DataFrame:
    """
    Forecast store-wide calendar uplift for the next N days.

    Uses historical uplift from Indian/US holidays, festivals, weekends, long weekends.
    """
    start = from_date or date.today()
    future_dates = [start + timedelta(days=i) for i in range(horizon_days)]
    cal = enrich_dates(future_dates, year=start.year)
    factors = _learn_uplift_factors(enriched_daily)

    rows = []
    for _, row in cal.iterrows():
        uplift = _uplift_for_calendar_row(row, factors)
        rows.append(
            {
                "date": row["date"],
                "day_name": row["day_name"],
                "day_type": row.get("day_type", "Weekday"),
                "us_holiday": row.get("us_holiday"),
                "indian_festival": row.get("indian_festival"),
                "is_long_weekend": row.get("is_long_weekend"),
                "demand_uplift_factor": uplift,
            }
        )
    return pd.DataFrame(rows)


def forecast_weather_uplift(
    enriched_daily: pd.DataFrame,
    *,
    forecast_days: int = 7,
) -> pd.DataFrame:
    """Next-week Okemos weather with learned demand uplift per weather type."""
    factors = _learn_uplift_factors(enriched_daily)
    weather = load_okemos_weather_forecast(forecast_days=forecast_days)
    if weather.empty:
        return pd.DataFrame()

    weather = weather.copy()
    weather["demand_uplift_factor"] = weather["weather_label"].apply(
        lambda lbl: factors.get(f"weather:{lbl}", factors.get("baseline", 1.0))
    )
    return weather


def forecast_demand_context(
    enriched_daily: pd.DataFrame,
    *,
    horizon_days: int = 14,
    weather_days: int = 7,
    from_date: date | None = None,
    apply_weather_to_orders: bool = False,
) -> pd.DataFrame:
    """
    Calendar outlook for reorder planning (+ optional weather display).

    Weather is joined for the Future outlook tab, but by default it does **not**
    raise order qty — EDA showed weak/negative weather correlation with sales.
    Strong signals are weekend / festival / holiday (calendar).
    """
    cal = forecast_calendar_uplift(enriched_daily, horizon_days=horizon_days, from_date=from_date)
    if cal.empty:
        return cal

    weather = forecast_weather_uplift(enriched_daily, forecast_days=weather_days)
    if weather.empty:
        cal["weather_label"] = None
        cal["temp_max_f"] = None
        cal["precip_in"] = None
        cal["weather_uplift_factor"] = None
        # Orders use calendar only (weekend / festival / holiday)
        cal["combined_uplift_factor"] = cal["demand_uplift_factor"]
        return cal

    weather["date"] = pd.to_datetime(weather["date"]).dt.normalize()
    cal["date"] = pd.to_datetime(cal["date"]).dt.normalize()
    merged = cal.merge(
        weather[["date", "weather_label", "temp_max_f", "temp_min_f", "precip_in", "demand_uplift_factor"]].rename(
            columns={"demand_uplift_factor": "weather_uplift_factor"}
        ),
        on="date",
        how="left",
    )
    # Default: do not let weak weather correlation inflate orders
    if apply_weather_to_orders:
        merged["combined_uplift_factor"] = merged.apply(
            lambda r: round(
                max(
                    float(r.get("demand_uplift_factor") or 1.0),
                    float(r.get("weather_uplift_factor") or 1.0),
                ),
                3,
            ),
            axis=1,
        )
    else:
        merged["combined_uplift_factor"] = merged["demand_uplift_factor"]
    return merged


def uplift_for_cover_window(context: pd.DataFrame, cover_days: int) -> float:
    """Max combined uplift in the next `cover_days` days."""
    return float(explain_uplift_for_cover_window(context, cover_days)["uplift_factor"])


def explain_uplift_for_cover_window(context: pd.DataFrame, cover_days: int) -> dict:
    """
    Return uplift factor + human-readable explanation for demos.

    Formula used on orders:
      uplifted_need = round(need × uplift_factor)
      Order qty     = round_to_pack(uplifted_need, pack)  # next case only if ≥50% fill

    uplift_factor = max(calendar uplift over the next `cover_days`)
    where each day's factor is learned as:
      factor = (avg sales on that day-type / festival / holiday) ÷ (overall avg daily sales)
    Weather is NOT included in the order uplift.
    """
    empty = {
        "uplift_factor": 1.0,
        "driver_date": None,
        "driver_day_name": None,
        "driver_day_type": None,
        "driver_festival": None,
        "driver_holiday": None,
        "driver_long_weekend": False,
        "window_days": int(cover_days),
        "formula": "uplifted_need = need × 1.0 (no calendar uplift)",
        "summary": "No calendar uplift (factor = 1.0).",
        "factors_note": (
            "Learned factors: day_type / Indian festival / US holiday / long weekend "
            "avg sales ÷ store baseline avg. Weather excluded from orders."
        ),
    }
    if context.empty:
        return empty
    col = "combined_uplift_factor" if "combined_uplift_factor" in context.columns else "demand_uplift_factor"
    window = context.head(max(int(cover_days), 1)).copy()
    if window.empty or col not in window.columns:
        return empty

    idx = window[col].astype(float).idxmax()
    row = window.loc[idx]
    factor = float(row[col]) if pd.notna(row[col]) else 1.0
    fest = row.get("indian_festival")
    hol = row.get("us_holiday")
    day_type = row.get("day_type") or "Weekday"
    day_name = row.get("day_name") or ""
    long_wk = bool(row.get("is_long_weekend"))
    driver_date = row.get("date")
    if hasattr(driver_date, "date"):
        driver_date = driver_date.date()
    elif driver_date is not None:
        driver_date = pd.Timestamp(driver_date).date()

    drivers = [str(day_type)]
    if pd.notna(fest) and str(fest).strip():
        drivers.append(f"festival:{fest}")
    if pd.notna(hol) and str(hol).strip():
        drivers.append(f"holiday:{hol}")
    if long_wk:
        drivers.append("long weekend")

    summary = (
        f"Cover window = next {int(cover_days)} day(s). "
        f"Uplift factor = {factor:.3f} from {driver_date} ({day_name}, {', '.join(drivers)}). "
        f"That is the max calendar uplift in the window."
    )
    formula = (
        f"uplift_factor = max(calendar uplift over next {int(cover_days)} days) = {factor:.3f} "
        f"(driver: {driver_date} / {day_name})"
        if factor > 1.0
        else "uplifted_need = need × 1.0 (no calendar uplift in cover window)"
    )

    return {
        "uplift_factor": round(factor, 3),
        "driver_date": str(driver_date) if driver_date else None,
        "driver_day_name": str(day_name) if day_name else None,
        "driver_day_type": str(day_type),
        "driver_festival": str(fest) if pd.notna(fest) and str(fest).strip() else None,
        "driver_holiday": str(hol) if pd.notna(hol) and str(hol).strip() else None,
        "driver_long_weekend": long_wk,
        "window_days": int(cover_days),
        "formula": formula,
        "summary": summary,
        "factors_note": empty["factors_note"],
    }


def adjusted_reorder_qty(raw_qty: float, uplift_factor: float, pack_size: int) -> tuple[int, int]:
    """
    Apply future uplift then round to pack.

    Returns (uplifted_raw, pack_rounded_order_qty).
    """
    from v2.inventory_math.pack_size import round_up_to_pack

    uplifted = max(0, int(round(raw_qty * uplift_factor)))
    return uplifted, round_up_to_pack(uplifted, pack_size)
