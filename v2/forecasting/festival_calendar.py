"""
India + USA festival / holiday calendar tags for demand uplift.

Tags attach to calendar dates so we can learn which SKUs spike on
weekends vs festival windows (not a blanket multiplier for every item).
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from typing import Iterable


# Fixed / civil dates by year. Lunar festivals use published approximate dates
# for 2024–2027; outside that range we fall back to month windows.
_YEAR_EVENTS: dict[int, list[tuple[str, date, date]]] = {
    # name, start (inclusive), end (inclusive)
    2024: [
        ("us_new_year", date(2024, 1, 1), date(2024, 1, 2)),
        ("in_republic_day", date(2024, 1, 26), date(2024, 1, 26)),
        ("us_mlk", date(2024, 1, 15), date(2024, 1, 15)),
        ("us_presidents", date(2024, 2, 19), date(2024, 2, 19)),
        ("in_holi", date(2024, 3, 24), date(2024, 3, 26)),
        ("us_easter", date(2024, 3, 29), date(2024, 3, 31)),
        ("us_memorial", date(2024, 5, 25), date(2024, 5, 27)),
        ("us_independence", date(2024, 7, 3), date(2024, 7, 5)),
        ("in_independence", date(2024, 8, 15), date(2024, 8, 15)),
        ("us_labor", date(2024, 8, 31), date(2024, 9, 2)),
        ("in_ganesh", date(2024, 9, 7), date(2024, 9, 17)),
        ("in_navratri_diwali", date(2024, 10, 3), date(2024, 11, 3)),
        ("us_thanksgiving", date(2024, 11, 27), date(2024, 12, 1)),
        ("us_christmas", date(2024, 12, 23), date(2024, 12, 26)),
        ("year_end", date(2024, 12, 30), date(2024, 12, 31)),
    ],
    2025: [
        ("us_new_year", date(2025, 1, 1), date(2025, 1, 2)),
        ("in_republic_day", date(2025, 1, 26), date(2025, 1, 26)),
        ("us_mlk", date(2025, 1, 20), date(2025, 1, 20)),
        ("us_presidents", date(2025, 2, 17), date(2025, 2, 17)),
        ("in_holi", date(2025, 3, 13), date(2025, 3, 15)),
        ("us_easter", date(2025, 4, 18), date(2025, 4, 20)),
        ("us_memorial", date(2025, 5, 24), date(2025, 5, 26)),
        ("us_independence", date(2025, 7, 3), date(2025, 7, 5)),
        ("in_independence", date(2025, 8, 15), date(2025, 8, 15)),
        ("us_labor", date(2025, 8, 30), date(2025, 9, 1)),
        ("in_ganesh", date(2025, 8, 27), date(2025, 9, 6)),
        ("in_navratri_diwali", date(2025, 9, 22), date(2025, 10, 22)),
        ("us_thanksgiving", date(2025, 11, 26), date(2025, 11, 30)),
        ("us_christmas", date(2025, 12, 23), date(2025, 12, 26)),
        ("year_end", date(2025, 12, 30), date(2025, 12, 31)),
    ],
    2026: [
        ("us_new_year", date(2026, 1, 1), date(2026, 1, 2)),
        ("in_republic_day", date(2026, 1, 26), date(2026, 1, 26)),
        ("us_mlk", date(2026, 1, 19), date(2026, 1, 19)),
        ("us_presidents", date(2026, 2, 16), date(2026, 2, 16)),
        ("in_holi", date(2026, 3, 3), date(2026, 3, 5)),
        ("us_easter", date(2026, 4, 3), date(2026, 4, 5)),
        ("us_memorial", date(2026, 5, 23), date(2026, 5, 25)),
        ("us_independence", date(2026, 7, 3), date(2026, 7, 5)),
        ("in_independence", date(2026, 8, 15), date(2026, 8, 15)),
        ("us_labor", date(2026, 9, 5), date(2026, 9, 7)),
        ("in_ganesh", date(2026, 9, 14), date(2026, 9, 24)),
        ("in_navratri_diwali", date(2026, 10, 11), date(2026, 11, 9)),
        ("us_thanksgiving", date(2026, 11, 25), date(2026, 11, 29)),
        ("us_christmas", date(2026, 12, 23), date(2026, 12, 26)),
        ("year_end", date(2026, 12, 30), date(2026, 12, 31)),
    ],
    2027: [
        ("us_new_year", date(2027, 1, 1), date(2027, 1, 2)),
        ("in_republic_day", date(2027, 1, 26), date(2027, 1, 26)),
        ("us_mlk", date(2027, 1, 18), date(2027, 1, 18)),
        ("us_presidents", date(2027, 2, 15), date(2027, 2, 15)),
        ("in_holi", date(2027, 3, 22), date(2027, 3, 24)),
        ("us_easter", date(2027, 3, 26), date(2027, 3, 28)),
        ("us_memorial", date(2027, 5, 29), date(2027, 5, 31)),
        ("us_independence", date(2027, 7, 3), date(2027, 7, 5)),
        ("in_independence", date(2027, 8, 15), date(2027, 8, 15)),
        ("us_labor", date(2027, 9, 4), date(2027, 9, 6)),
        ("in_ganesh", date(2027, 9, 4), date(2027, 9, 14)),
        ("in_navratri_diwali", date(2027, 9, 30), date(2027, 10, 30)),
        ("us_thanksgiving", date(2027, 11, 24), date(2027, 11, 28)),
        ("us_christmas", date(2027, 12, 23), date(2027, 12, 26)),
        ("year_end", date(2027, 12, 30), date(2027, 12, 31)),
    ],
}

# Fallback month windows when year table missing (lunar-ish seasons)
_FALLBACK_WINDOWS: list[tuple[str, int, int, int, int]] = [
    # name, start_month, start_day, end_month, end_day
    ("in_holi_season", 3, 1, 3, 25),
    ("in_navratri_diwali", 10, 1, 11, 15),
    ("us_july4_week", 7, 1, 7, 7),
    ("us_thanksgiving_season", 11, 20, 11, 30),
    ("us_christmas_season", 12, 20, 12, 31),
]


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5  # Sat=5, Sun=6


def _in_range(d: date, start: date, end: date) -> bool:
    return start <= d <= end


@lru_cache(maxsize=32)
def _events_for_year(year: int) -> tuple[tuple[str, date, date], ...]:
    return tuple(_YEAR_EVENTS.get(year, ()))


def festival_tags_for_date(d: date) -> list[str]:
    """Return festival/holiday tag names active on date ``d``."""
    tags: list[str] = []
    for name, start, end in _events_for_year(d.year):
        if _in_range(d, start, end):
            tags.append(name)
    if not tags:
        for name, sm, sd, em, ed in _FALLBACK_WINDOWS:
            start = date(d.year, sm, sd)
            end = date(d.year, em, ed)
            if _in_range(d, start, end):
                tags.append(name)
    return tags


def calendar_labels(d: date) -> list[str]:
    """Weekend + festival labels for a date."""
    labels: list[str] = []
    if is_weekend(d):
        labels.append("weekend")
    labels.extend(festival_tags_for_date(d))
    return labels


def expand_date_tags(dates: Iterable[date]) -> dict[date, list[str]]:
    return {d: calendar_labels(d) for d in dates}


def daterange(start: date, end: date) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=1)
    return out
