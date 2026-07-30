"""Normalize product names for inventory ↔ vendor catalog matching and similar-item grouping."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# Expand abbreviations before tokenizing
WORD_ALIASES: dict[str, str] = {
    "HLD": "HALDIRAM",
    "HALDIRAMS": "HALDIRAM",
    "LX": "LAXMI",
    "PK": "PK",
    "FZ": "FROZEN",
    "FRZ": "FROZEN",
    "FR": "FROZEN",
    "GUVAR": "GUVAR",
    "GUWAR": "GUVAR",
    "GUVARBEANS": "GUVAR",
    "GM": "G",
    "GMS": "G",
    "GR": "G",
    "GRAM": "G",
    "GRAMS": "G",
    "GRM": "G",
    "KGS": "KG",
    "KILO": "KG",
    "LBS": "LB",
    "LTR": "L",
    "LT": "L",
    "LITER": "L",
    "LITRE": "L",
    "LITERS": "L",
    "LITRES": "L",
    "MLT": "ML",
    "MILLILITER": "ML",
    "MILLILITRE": "ML",
    "OZ": "OZ",
    "OUNCE": "OZ",
    "OUNCES": "OZ",
    "PC": "PCS",
    "PCS": "PCS",
    "PIECE": "PCS",
    "PIECES": "PCS",
    "CT": "PCS",
    "COUNT": "PCS",
    "BLNCHD": "BLANCHED",
    "BLN": "BLANCHED",
    "RAW": "RAW",
    "JMB": "JUMBO",
    "JUMBO": "JUMBO",
    "PREM": "PREMIUM",
    "PREMIUM": "PREMIUM",
    "CP": "COLD",
    "CPR": "COLD",
    "PRESSED": "PRESSED",
    "VPK": "VPK",
    "MM": "MOTHER",
    "MTR": "MTR",
    "MDH": "MDH",
    "DEEP": "DEEP",
    "SWAGAT": "SWAGAT",
    "MEHARBAN": "MEHARBAN",
    "ANNABYTES": "PK",
    "ANNABYTESPK": "PK",
    "FMLY": "FAMILY",
    "FAMILY": "FAMILY",
    "CATERING": "CATERING",
    "POROTTA": "PAROTTA",
    "PARATA": "PAROTTA",
    "PARATHA": "PARATHA",
}

# Known brand tokens stripped when building product signature (same item, different brand)
BRAND_TOKENS: frozenset[str] = frozenset(
    {
        "HALDIRAM",
        "HLD",
        "LAXMI",
        "LX",
        "PK",
        "DEEP",
        "SWAGAT",
        "MEHARBAN",
        "ANNAPURNA",
        "MOGHUL",
        "PREMIER",
        "TATA",
        "MDH",
        "MTR",
        "BRU",
        "CADBURY",
        "NESTLE",
        "AMUL",
        "VADILAL",
        "HALDIRAMS",
        "MOTHER",
        "MM",
        "GHARANA",
        "JANAKI",
        "SOUTHIE",
        "ARYA",
        "PRIYEMS",
        "EVEREST",
        "HIMALAYAN",
        "DULHAN",
        "BRAR",
        "HOS",
        "SOHAM",
        "TIRANGA",
        "ASHOKA",
        "ANAND",
        "DD",
        "DAILY",
        "DELIGHT",
        "BANNO",
        "COLONEL",
        "KHAZANA",
        "DECCAN",
        "KARACHI",
        "RAJBHOG",
        "SAKTHI",
        "TDH",
        "GOLI",
        "LOTUS",
        "LAYS",
        "KURKURE",
        "ANAGANAGA",
        "SUNFINA",
        "BABCO",
        "NAMASTE",
        "CHETAK",
        "DHARTI",
        "VISWAS",
        "AASHIRVAAD",
        "AK",
    }
)

# Form / state words stripped so FRZ METHI LEAVES matches METHI LEAVES across brands
FORM_TOKENS: frozenset[str] = frozenset(
    {
        "FROZEN",
        "FRZ",
        "FZ",
        "FR",
        "CANNED",
        "DRY",
        "POWDER",
        "PWD",
        "INSTANT",
    }
)

SIZE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(G|KG|LB|L|ML|OZ|PCS|PC|LT|LTR)\b",
    re.I,
)
PACK_PATTERN = re.compile(r"\d+\s*[xX×]\s*\d+", re.I)
NOISE_TOKENS = frozenset({"THE", "AND", "WITH", "W", "OF", "IN", "FOR", "A", "AN", "NEW", "NOT", "STOCK", "X"})


def _squash(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def expand_aliases(text: str) -> str:
    """Replace known abbreviations and normalize spacing."""
    t = re.sub(r"\s+", " ", str(text).upper().strip())
    t = re.sub(r"(\d)([A-Z])", r"\1 \2", t)
    t = re.sub(r"([A-Z])(\d)", r"\1 \2", t)
    tokens = []
    for raw in re.split(r"[\s/\-_,]+", t):
        if not raw:
            continue
        tokens.append(WORD_ALIASES.get(raw, raw))
    return " ".join(tokens)


def extract_size_token(text: str) -> str | None:
    """Return canonical size like 400G, 4LB, 1KG."""
    t = expand_aliases(text)
    matches = SIZE_PATTERN.findall(t)
    if not matches:
        return None
    # Prefer last size token (often the pack size in product names)
    qty, unit = matches[-1]
    qty_f = float(qty)
    qty_s = str(int(qty_f)) if qty_f == int(qty_f) else str(qty_f)
    return f"{qty_s}{unit.upper()}"


def tokenize(text: str) -> list[str]:
    t = expand_aliases(text)
    tokens = []
    for raw in re.split(r"[\s/\-_,]+", t):
        if not raw or raw in NOISE_TOKENS:
            continue
        if PACK_PATTERN.fullmatch(raw):
            continue
        if raw.isdigit():
            continue
        tokens.append(raw)
    return tokens


def normalize_product_name(name: str, *, size_field: str | None = None) -> str:
    """Alphanumeric key after alias expansion — for exact-ish comparisons."""
    combined = f"{name} {size_field or ''}".strip()
    expanded = expand_aliases(combined)
    return _squash(expanded)


def norm_name(name: str, *, size_field: str | None = None) -> str:
    """Backward-compatible alias used by catalog loader."""
    return normalize_product_name(name, size_field=size_field)


def _strip_sizes(text: str) -> str:
    """Remove size tokens so they don't pollute the product signature."""
    t = expand_aliases(text)
    t = SIZE_PATTERN.sub(" ", t)
    t = PACK_PATTERN.sub(" ", t)
    # leftover case markers like "12 X" after stripping "1 LB" from "12 x 1 lb"
    t = re.sub(r"\b\d+\s*X\b", " ", t, flags=re.I)
    t = re.sub(r"\bX\b", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def product_signature(name: str, *, size_field: str | None = None) -> dict[str, Any]:
    """
    Build a cross-brand product signature for similar-item grouping.

    Example: LX PEANUTS JUMBO 4 LB and MEHARBAN PEANUTS 3LB get different signatures
    (different size). SWAGAT PEANUT RAW 400G vs SWAGAT PEANUT BLANCHED 400G differ
    on RAW vs BLANCHED.
    """
    combined = f"{name} {size_field or ''}".strip()
    size = extract_size_token(combined)
    core_text = _strip_sizes(combined)
    tokens = tokenize(core_text)

    brand = ""
    core_tokens: list[str] = []
    for i, tok in enumerate(tokens):
        if i == 0 and tok in BRAND_TOKENS:
            brand = tok
            continue
        if tok in BRAND_TOKENS or tok in FORM_TOKENS:
            continue
        core_tokens.append(tok)

    if not core_tokens and tokens:
        core_tokens = [t for t in tokens if t not in BRAND_TOKENS and t not in FORM_TOKENS]
        if not core_tokens:
            core_tokens = tokens[1:] if tokens[0] in BRAND_TOKENS else tokens

    item_key = "|".join(core_tokens).upper()
    signature = item_key
    if size:
        signature = f"{signature}|{size}"

    return {
        "brand": brand,
        "size": size,
        "core_tokens": core_tokens,
        "item_key": item_key,
        "signature_key": signature.upper(),
        "norm_name": normalize_product_name(name, size_field=size_field),
    }


def match_score(a: str, b: str, *, size_a: str | None = None, size_b: str | None = None) -> float:
    """
    Score 0–100 for inventory description vs vendor catalog name.
    Requires size match when both sides have a parseable size — unless core
    product tokens already match strongly (case pack vs unit size wording).
    """
    sig_a = product_signature(a, size_field=size_a)
    sig_b = product_signature(b, size_field=size_b)

    ta = set(sig_a["core_tokens"])
    tb = set(sig_b["core_tokens"])
    overlap = len(ta & tb) / max(len(ta | tb), 1) if ta and tb else 0.0
    token_score = overlap * 100

    size_mismatch = bool(sig_a["size"] and sig_b["size"] and sig_a["size"] != sig_b["size"])
    if size_mismatch and token_score < 75:
        return 0.0

    na = sig_a["norm_name"]
    nb = sig_b["norm_name"]
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    if na in nb or nb in na:
        return 92.0

    if ta and tb:
        # Kerala vs Garlic (same generic PAROTTA): do not let string similarity override
        if len(ta) >= 2 and len(tb) >= 2 and len(ta & tb) <= 1 and overlap < 0.55:
            return round(token_score, 1)
        if token_score >= 55:
            score = round(token_score, 1)
            if size_mismatch:
                score = min(score, 88.0)
            return score

    # Last resort string similarity — only when tokens are weak/short
    if len(ta) >= 2 and len(tb) >= 2 and overlap < 0.4:
        return round(token_score, 1)
    return round(SequenceMatcher(None, na, nb).ratio() * 100, 1)


def best_catalog_match(
    inv_desc: str,
    catalog_names: list[str],
    *,
    inv_size: str | None = None,
    min_score: float = 72.0,
) -> tuple[str | None, float]:
    """Return best catalog product_name and score for an inventory description."""
    best_name: str | None = None
    best = 0.0
    for cat in catalog_names:
        score = match_score(inv_desc, cat, size_a=inv_size)
        if score > best:
            best = score
            best_name = cat
    if best >= min_score:
        return best_name, best
    return None, best
