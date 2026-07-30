"""Unit tests for inventory mathematics module."""

from v2.inventory_math import calculate_dynamic_reorder_point, calculate_eoq, calculate_safety_stock


def test_calculate_safety_stock_positive():
    ss = calculate_safety_stock(10, 2, 7)
    assert ss > 0


def test_calculate_dynamic_reorder_point():
    rop = calculate_dynamic_reorder_point(10, 7, 20)
    assert rop == 90.0


def test_calculate_eoq():
    eoq = calculate_eoq(3650)
    assert eoq > 0
