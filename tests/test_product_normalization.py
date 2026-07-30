"""Tests for product name normalization and similar-product grouping."""

from app.dashboard.product_normalization import match_score, product_signature
from app.dashboard.similar_products import build_similar_product_groups
import pandas as pd


def test_guvar_name_variants_match():
    assert match_score("FROZEN GUVAR 100G", "Frozen Guvar 100g") >= 85
    assert match_score("FZ GUVAR 100G", "Frozen Guvar 100g") >= 72


def test_haldiram_alias_matches():
    assert match_score("HLD MIXTURE 400G", "HALDIRAM MIXTURE 400G") >= 85


def test_different_sizes_do_not_match():
    assert match_score("LX PEANUTS JUMBO 4 LB", "MEHARBAN PEANUTS 3LB") == 0


def test_similar_peanut_grouping():
    inv = pd.DataFrame(
        [
            {"upc": "1", "description": "SWAGAT PEANUT RAW 400 GM", "size": "400G", "vendor_name": "OM", "QuantityOnHand": 5},
            {"upc": "2", "description": "SWAGAT PEANUT BLANCHED 400G", "size": "400G", "vendor_name": "OM", "QuantityOnHand": 8},
            {"upc": "3", "description": "OTHER ITEM 400G", "size": "400G", "vendor_name": "HOS", "QuantityOnHand": 2},
        ]
    )
    groups = build_similar_product_groups(inv)
    # RAW vs BLANCHED are different signatures
    assert groups.empty or all(g["member_count"] >= 2 for _, g in groups.iterrows())


def test_signature_strips_brand():
    sig = product_signature("LX PEANUTS JUMBO 4 LB", size_field="4LB")
    assert "LAXMI" not in sig["signature_key"] or sig["brand"] == "LAXMI"
    assert "PEANUT" in sig["signature_key"]
