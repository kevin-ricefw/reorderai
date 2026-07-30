"""Round reorder quantities to vendor case/pack sizes."""

from __future__ import annotations

import math

# Next case is suggested only when leftover need fills at least this share of a case.
DEFAULT_MIN_FILL_RATIO = 0.5


def normalize_pack_size(pack: float | int | None) -> int:
    """Return valid pack size (minimum 1)."""
    if pack is None:
        return 1
    try:
        value = int(float(pack))
    except (TypeError, ValueError):
        return 1
    return max(value, 1)


def round_up_to_pack(
    raw_qty: float | int,
    pack_size: float | int | None,
    *,
    min_fill_ratio: float = DEFAULT_MIN_FILL_RATIO,
) -> int:
    """
    Round quantity to pack/case multiples using a minimum fill threshold.

    The next case is ordered only when the leftover need is at least
    ``min_fill_ratio`` of a case (default 50%).

    Examples (pack=20):
      15 → 20  (75% of 1st case → order 1)
      23 → 20  (only 15% into 2nd case → stay at 1)
      25 → 20  (25% into 2nd case → stay at 1)
      30 → 40  (50% into 2nd case → order 2)
      31 → 40  (above 50% → order 2)
    """
    qty = max(float(raw_qty), 0.0)
    pack = normalize_pack_size(pack_size)
    if qty <= 0:
        return 0
    if pack <= 1:
        return int(math.ceil(qty))

    threshold = min(max(float(min_fill_ratio), 0.0), 1.0)
    exact = qty / pack
    full_cases = int(math.floor(exact))
    frac = exact - full_cases
    if frac >= threshold:
        cases = full_cases + 1
    else:
        cases = full_cases
    return int(cases * pack)


def pack_units_ordered(order_qty: int, pack_size: float | int | None) -> float:
    """Number of cases/boxes for display."""
    pack = normalize_pack_size(pack_size)
    if pack <= 1 or order_qty <= 0:
        return float(order_qty)
    return round(order_qty / pack, 2)
