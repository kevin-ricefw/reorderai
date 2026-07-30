"""Tests for loose produce exclusion rules."""

from v2.analytics.dashboard_constants import DASHBOARD_NOTE
from v2.analytics.produce_filter import (
    filter_dataframe_excluding_produce,
    is_loose_fresh_produce,
    is_produce_vendor,
)


def test_exclude_simple_loose_produce():
    assert is_loose_fresh_produce("Dosakai", "PRODUCE") is True
    assert is_loose_fresh_produce("Chikoo", "PRODUCE") is True
    assert is_loose_fresh_produce("Fresh Eggplant", "PRODUCE") is True
    assert is_loose_fresh_produce("Fresh Ginger", "PRODUCE") is True
    assert is_loose_fresh_produce("Thai Chilli", "PRODUCE") is True
    assert is_loose_fresh_produce("Sweet Potato", "PRODUCE") is True
    assert is_loose_fresh_produce("Eddo", "PRODUCE") is True
    assert is_loose_fresh_produce("Tindora", "PRODUCE") is True
    assert is_loose_fresh_produce("Indian Eggplant", "PRODUCE") is True
    assert is_loose_fresh_produce("Roma Tomato", "PRODUCE") is True
    assert is_loose_fresh_produce("Curry Leaves", "PRODUCE") is True
    assert is_loose_fresh_produce("Methi Leaves", "PRODUCE") is True
    assert is_loose_fresh_produce("FRESH DESI OKRA", "PRODUCE") is True


def test_keep_packaged_weights_and_counts():
    assert is_loose_fresh_produce("Grated Coconut 500G", "PRODUCE") is False
    assert is_loose_fresh_produce("FRESH GRATED COCONUT 500G", "PRODUCE") is False
    assert is_loose_fresh_produce("FRESH CARROT 3 LB", "PRODUCE") is False
    assert is_loose_fresh_produce("FRESH RED ONION 2 LB", "PRODUCE") is False
    assert is_loose_fresh_produce("FRESH GARLIC 5 PC", "PRODUCE") is False
    assert is_loose_fresh_produce("FRESH CAULIFLOWER", "PRODUCE", size="7 PC") is False
    assert is_loose_fresh_produce("BITTERMELON INDIAN 25-30 LB") is False
    assert is_loose_fresh_produce("Mixed Vegetables 1LB", "FROZEN VEGETABLE") is False


def test_keep_frozen_canned_and_grocery():
    assert is_loose_fresh_produce("Frozen Okra 500G", "FROZEN VEGETABLE") is False
    assert is_loose_fresh_produce("Karela 300G", "FROZEN VEGETABLE") is False
    assert is_loose_fresh_produce("PK KARELA 454G", "FROZEN VEGETABLE") is False
    assert is_loose_fresh_produce("VADILAL MIXED VEGETABLES 908G", "FROZEN VEGETABLE") is False
    assert is_loose_fresh_produce("SAMOSA 3PC", "PICKLES,SAUCE AND SOUPS") is False
    assert is_loose_fresh_produce("ROYAL BASMATI RICE 20LB", "RICE") is False
    assert is_loose_fresh_produce("MDH GARAM MASALA 100G", "SPICES") is False
    assert is_loose_fresh_produce("Roasty Tasty Mint Lachha", "SNACKS") is False


def test_produce_vendors_excluded_entirely():
    assert is_produce_vendor("JALARAM") is True
    assert is_produce_vendor("CARLOS") is True
    from v2.analytics.produce_filter import should_exclude_from_analysis

    assert should_exclude_from_analysis("FRESH CARROT 3 LB", "PRODUCE", vendor_name="CARLOS") is True
    assert should_exclude_from_analysis("FRESH DESI OKRA", "PRODUCE", vendor_name="JALARAM") is True


def test_dashboard_note():
    assert "Loose produce SKUs excluded" in DASHBOARD_NOTE
    assert "300G" in DASHBOARD_NOTE
