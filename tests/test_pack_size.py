"""Tests for pack/case rounding."""

from v2.inventory_math.pack_size import normalize_pack_size, pack_units_ordered, round_up_to_pack


def test_round_up_to_pack_half_case_threshold():
    # pack=20: next case only when leftover need >= 50% of a case
    assert round_up_to_pack(15, 20) == 20   # 75% of 1st case
    assert round_up_to_pack(20, 20) == 20
    assert round_up_to_pack(21, 20) == 20   # only 5% into 2nd → stay at 1
    assert round_up_to_pack(23, 20) == 20
    assert round_up_to_pack(25, 20) == 20
    assert round_up_to_pack(29, 20) == 20   # 45% into 2nd
    assert round_up_to_pack(30, 20) == 40   # exactly 50% → 2 cases
    assert round_up_to_pack(31, 20) == 40
    assert round_up_to_pack(0, 20) == 0
    assert round_up_to_pack(5, 1) == 5
    assert round_up_to_pack(9, 20) == 0     # under 50% of first case
    assert round_up_to_pack(10, 20) == 20   # exactly 50% of first case


def test_normalize_pack_size():
    assert normalize_pack_size(None) == 1
    assert normalize_pack_size(0) == 1
    assert normalize_pack_size(25) == 25


def test_pack_units_ordered():
    assert pack_units_ordered(20, 20) == 1.0
    assert pack_units_ordered(40, 20) == 2.0
