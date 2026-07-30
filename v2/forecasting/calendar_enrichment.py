"""Calendar features: weekday, weekend, US holidays, long weekends, Indian festivals."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Major Indian festivals and observances for 2026 (Okemos store serves Indian community)
INDIAN_FESTIVALS_2026: dict[date, str] = {
    date(2026, 1, 14): "Makar Sankranti / Pongal",
    date(2026, 1, 26): "Republic Day (India)",
    date(2026, 2, 15): "Maha Shivaratri",
    date(2026, 3, 3): "Holi",
    date(2026, 3, 19): "Ugadi / Gudi Padwa",
    date(2026, 3, 20): "Eid al-Fitr (approx)",
    date(2026, 3, 27): "Rama Navami",
    date(2026, 4, 12): "Hanuman Jayanti",
    date(2026, 4, 21): "Akshaya Tritiya",
    date(2026, 8, 9): "Raksha Bandhan",
    date(2026, 8, 15): "Independence Day (India)",
    date(2026, 8, 26): "Onam",
    date(2026, 9, 4): "Janmashtami",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 9): "Navratri begins",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 8): "Diwali",
    date(2026, 11, 11): "Bhai Dooj",
    date(2026, 11, 24): "Gurpurab",
}

US_FEDERAL_HOLIDAYS_2026: dict[date, str] = {
    date(2026, 1, 1): "New Year's Day",
    date(2026, 1, 19): "Martin Luther King Jr. Day",
    date(2026, 2, 16): "Presidents' Day",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth",
    date(2026, 7, 4): "Independence Day (US)",
    date(2026, 9, 7): "Labor Day",
    date(2026, 10, 12): "Columbus Day",
    date(2026, 11, 11): "Veterans Day",
    date(2026, 11, 26): "Thanksgiving",
    date(2026, 12, 25): "Christmas",
}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth weekday of month (weekday: Mon=0)."""
    d = date(year, month, 1)
    count = 0
    while d.month == month:
        if d.weekday() == weekday:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    raise ValueError("weekday not found")


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def us_holidays_for_year(year: int) -> dict[date, str]:
    """US federal holidays; fixed + computed for any year."""
    if year == 2026:
        return dict(US_FEDERAL_HOLIDAYS_2026)
    return {
        date(year, 1, 1): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        _last_weekday(year, 5, 0): "Memorial Day",
        date(year, 6, 19): "Juneteenth",
        date(year, 7, 4): "Independence Day (US)",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 10, 0, 2): "Columbus Day",
        date(year, 11, 11): "Veterans Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        date(year, 12, 25): "Christmas",
    }


def indian_festivals_for_year(year: int) -> dict[date, str]:
    if year == 2026:
        return dict(INDIAN_FESTIVALS_2026)
    return {}


def _is_long_weekend_day(d: date, holidays: set[date]) -> bool:
    """True if date is part of a 3+ day break involving a holiday and weekend."""
    if d in holidays:
        wd = d.weekday()
        if wd == 4:  # Friday holiday
            return True
        if wd == 0:  # Monday holiday
            return True
        if wd in (5, 6):
            return (d - timedelta(days=1)) in holidays or (d + timedelta(days=1)) in holidays
    if d.weekday() in (5, 6):
        fri = d - timedelta(days=d.weekday() - 4) if d.weekday() == 5 else d - timedelta(days=2)
        mon = d + timedelta(days=7 - d.weekday()) if d.weekday() == 6 else d + timedelta(days=1)
        if fri in holidays or mon in holidays:
            return True
    return False


def enrich_dates(dates: pd.Series | list[date] | pd.DatetimeIndex, *, year: int = 2026) -> pd.DataFrame:
    """
    Build calendar dimension for each date.

    Columns: date, day_name, day_of_week, is_weekend, is_weekday,
             us_holiday, indian_festival, day_type, is_long_weekend
    """
    us = us_holidays_for_year(year)
    indian = indian_festivals_for_year(year)
    all_holidays = set(us.keys()) | set(indian.keys())

    if isinstance(dates, pd.DatetimeIndex):
        series = pd.Series(dates.normalize())
    else:
        series = pd.to_datetime(pd.Series(dates), errors="coerce").dt.normalize()

    rows = []
    for ts in series.dropna().unique():
        d = ts.date() if hasattr(ts, "date") else ts
        dow = d.weekday()
        us_name = us.get(d, "")
        fest = indian.get(d, "")
        is_weekend = dow >= 5
        is_long = _is_long_weekend_day(d, all_holidays)

        if fest:
            day_type = "Indian Festival"
        elif us_name:
            day_type = "US Holiday"
        elif is_long:
            day_type = "Long Weekend"
        elif is_weekend:
            day_type = "Weekend"
        else:
            day_type = "Weekday"

        rows.append(
            {
                "date": pd.Timestamp(d),
                "day_name": DAY_NAMES[dow],
                "day_of_week": dow,
                "is_weekend": is_weekend,
                "is_weekday": not is_weekend,
                "us_holiday": us_name or None,
                "indian_festival": fest or None,
                "is_long_weekend": is_long,
                "day_type": day_type,
            }
        )

    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def merge_calendar_features(df: pd.DataFrame, date_col: str = "date", *, year: int = 2026) -> pd.DataFrame:
    """Left-join calendar features onto a dataframe with a date column."""
    if df.empty or date_col not in df.columns:
        return df
    cal = enrich_dates(df[date_col], year=year)
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
    return out.merge(cal, on="date", how="left")
