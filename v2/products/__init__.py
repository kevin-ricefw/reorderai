"""Product name helpers (brand stripping, signatures)."""

from v2.products.product_normalization import product_signature
from v2.products.same_item_brands import build_other_brands_map

__all__ = ["product_signature", "build_other_brands_map"]
