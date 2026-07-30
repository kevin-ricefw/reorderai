"""Vendor schedule → planning cover days (14-day fallback when unknown)."""

from app.dashboard.vendor_catalog_loader import (
    DEFAULT_NO_SCHEDULE_COVER_DAYS,
    resolve_planning_cover_days,
)


def test_known_schedule_uses_lead_time():
    lead, sched = resolve_planning_cover_days("HOS (LAXMI)")
    assert sched["has_known_schedule"] is True
    assert lead == sched["lead_time_days"]
    assert lead != DEFAULT_NO_SCHEDULE_COVER_DAYS or lead == DEFAULT_NO_SCHEDULE_COVER_DAYS


def test_unknown_schedule_defaults_to_14_days():
    lead, sched = resolve_planning_cover_days("TIRANGA FOODS")
    assert sched["has_known_schedule"] is False
    assert lead == DEFAULT_NO_SCHEDULE_COVER_DAYS
    assert sched["lead_time_days"] == DEFAULT_NO_SCHEDULE_COVER_DAYS


def test_random_vendor_without_schedule_gets_14_days():
    lead, sched = resolve_planning_cover_days("VADILAL")
    assert sched["has_known_schedule"] is False
    assert lead == 14


def test_explicit_cover_overrides_schedule():
    lead, sched = resolve_planning_cover_days("HOS (LAXMI)", explicit_cover_days=21)
    assert lead == 21
    assert sched["has_known_schedule"] is True
