"""Brand → order-form vendor ownership (POS vendor_name is often wrong)."""

from __future__ import annotations

# Brands on each distributor's order Excel that frequently sit under another POS vendor
# (e.g. ANAND under BABCO FOODS but ordered from ANNAPURNA.xlsx).
ORDER_FORM_BRAND_OWNERS: dict[str, tuple[str, ...]] = {
    "ANNAPURNA": (
        "ANAND",
        "ASHOKA",
        "BRAR",
        "BRAJ",
        "RAJBHOG",
        "DECCAN",
        "KHAZANA",
        "BEDEKAR",
        "TDH",
        "SAKTHI",
        "ANAGANAGA",
        "GOLI",
        "BANNO",
        "LOTUS",
        "KARACHI",
        # MDH omitted — split Annapurna/Premier; catalog fuzzy match only
    ),
    "HOS": (
        "LAXMI",
        "LX",
        "COLONEL",
        "CADBURY",
        "NESTLE",
        "SIKANDAR",
        "GG",
    ),
    "PREMIER": (
        "HALDIRAM",
        "HLD",
        "MTR",
        "NANAK",
        # MDH omitted — split with Annapurna; catalog fuzzy match only
    ),
    "MOGHUL": (
        "SHAN",
        "GRB",
        "DABUR",
    ),
    "OM": (
        "SWAGAT",
    ),
    "VADILAL": (
        "VADILAL",
    ),
    "DHARTI": (
        "DHARTI",
        "JIVA",
    ),
    "CHETAK_(DEEP)": (
        "DEEP",
        "CHETAK",
    ),
    "EVEREST": (
        "AASHIRVAAD",
    ),
}

# Tokens that look like a brand but must NOT force remaps
BRAND_FALSE_POSITIVES: frozenset[str] = frozenset(
    {
        "LAXMINARAYAN",
        "DEEPAM",
        "ANANDHAM",
    }
)
