"""Load vendor catalogs and delivery schedule from project files."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from app.dashboard.product_normalization import best_catalog_match, match_score, norm_name, product_signature
from app.dashboard.vendor_brand_owners import BRAND_FALSE_POSITIVES, ORDER_FORM_BRAND_OWNERS
from config.data_paths import PROJECT_ROOT, SCHEDULE_PATH, VENDORS_DIR
from v2.analytics.produce_filter import PRODUCE_VENDOR_NAMES

COVERAGE_PATH = PROJECT_ROOT / "outputs" / "analytics" / "vendor_catalog_coverage.csv"

# When cut-off / delivery / lead time unknown → plan reorder for this many days
DEFAULT_NO_SCHEDULE_COVER_DAYS = 14

_UNKNOWN_CUTOFF = frozenset({"", "CONTACT VENDOR", "NAN", "NO SCHEDULE — USING 14-DAY COVER"})
_UNKNOWN_DELIVERY = frozenset({"", "TBD", "NAN"})
# "Get update from vendor" is still a real monthly schedule row — keep as known when cutoff exists.

# Original 8 vendors with dedicated PDF / custom Excel loaders
LEGACY_VENDORS: list[dict[str, Any]] = [
    {
        "key": "ANNAPURNA",
        "inventory_names": ["ANNAPURNA"],
        "schedule_name": "ANNAPURNA FOODS",
        "catalog_file": "ANNAPURNA.xlsx",
        "catalog_type": "xlsx",
    },
    {
        "key": "EVEREST",
        "inventory_names": ["EVEREST TRADERS"],
        "schedule_name": "EVEREST TRADERS",
        "catalog_file": "EVEREST.xlsx",
        "catalog_type": "xlsx",
        "pdf_source": "EVEREST.pdf",
    },
    {
        "key": "HOS",
        "inventory_names": ["HOS (LAXMI)"],
        "schedule_name": "HOS",
        "catalog_file": "HOS.xlsx",
        "catalog_type": "xlsx",
    },
    {
        "key": "MOGHUL",
        "inventory_names": ["MOGHUL FOODS"],
        "schedule_name": "MOGHUL FOODS",
        "catalog_file": "MOGHUL.xlsx",
        "catalog_type": "xlsx",
    },
    {
        "key": "OM",
        "inventory_names": ["OM PRODUCE (SWAGAT)"],
        "schedule_name": "OM PRODUCE",
        "catalog_file": "OM.xlsx",
        "catalog_type": "xlsx",
        "pdf_source": "OM.pdf",
    },
    {
        "key": "PREMIER",
        "inventory_names": ["PREMIER FOODS"],
        "schedule_name": "PREMIER",
        "catalog_file": "PREMIER.xlsx",
        "catalog_type": "xlsx",
        "pdf_source": "PREMIERR.pdf",
    },
    {
        "key": "SOHAM",
        "inventory_names": ["SOHAM"],
        "schedule_name": "SOHAM",
        "catalog_file": "SOHAM.xlsx",
        "catalog_type": "xlsx",
        "pdf_source": "SOHAM FOODS.pdf",
    },
    {
        "key": "VADILAL",
        "inventory_names": ["VADILAL"],
        "schedule_name": "VADILAL",
        "catalog_file": "VADILAL.xlsx",
        "catalog_type": "xlsx",
    },
    {
        "key": "GAZAB",
        "inventory_names": ["UNITED TRADERS (GAZAB)"],
        "schedule_name": "GAZAB",
        "catalog_file": "UNITED_TRADERS_(GAZAB).xlsx",
        "catalog_type": "xlsx",
    },
    {
        "key": "DHARTI",
        "inventory_names": ["DHARTI FOODS"],
        "schedule_name": "DHARTI FOODS",
        "catalog_file": "DHARTI_FOODS.xlsx",
        "catalog_type": "xlsx",
    },
    {
        "key": "TIRANGA",
        "inventory_names": ["TIRANGA FOODS"],
        "schedule_name": None,
        "catalog_file": "TIRANGA.xlsx",
        "catalog_type": "xlsx",
    },
]

# Backward-compatible alias (legacy scripts)
TRACKED_VENDORS = LEGACY_VENDORS

# Inventory vendor_name -> catalog filename
KNOWN_CATALOG_FILES: dict[str, str] = {
    "ANNAPURNA": "ANNAPURNA.xlsx",
    "EVEREST TRADERS": "EVEREST.xlsx",
    "HOS (LAXMI)": "HOS.xlsx",
    "MOGHUL FOODS": "MOGHUL.xlsx",
    "OM PRODUCE (SWAGAT)": "OM.xlsx",
    "PREMIER FOODS": "PREMIER.xlsx",
    "SOHAM": "SOHAM.xlsx",
    "TIRANGA FOODS": "TIRANGA.xlsx",
    "VADILAL": "VADILAL.xlsx",
    "UNITED TRADERS (GAZAB)": "UNITED_TRADERS_(GAZAB).xlsx",
    "DHARTI FOODS": "DHARTI_FOODS.xlsx",
}


def _vendor_key_from_name(name: str) -> str:
    safe = re.sub(r"[^\w\s()-]", "", str(name).upper())
    return re.sub(r"\s+", "_", safe.strip())


def _vendor_key_from_catalog(catalog_file: str) -> str:
    return Path(catalog_file).stem.upper()


@lru_cache(maxsize=1)
def get_all_store_vendors() -> tuple[dict[str, Any], ...]:
    """
    All grocery vendors with catalog files in VENDORS/ (from coverage CSV + legacy metadata).

    Excludes produce-only suppliers JALARAM and CARLOS.
    """
    vendors_by_name: dict[str, dict[str, Any]] = {}

    for v in LEGACY_VENDORS:
        for name in v["inventory_names"]:
            vendors_by_name[name] = {**v, "legacy": True}

    if COVERAGE_PATH.exists():
        cov = pd.read_csv(COVERAGE_PATH)
        for _, row in cov.iterrows():
            vendor_name = str(row.get("vendor_name", "")).strip()
            catalog_file = str(row.get("catalog_file", "")).strip()
            if not vendor_name or not catalog_file or catalog_file.lower() == "nan":
                continue
            if vendor_name.upper() in PRODUCE_VENDOR_NAMES or vendor_name == "Unknown":
                continue
            if vendor_name in vendors_by_name:
                continue
            if not (VENDORS_DIR / catalog_file).exists():
                continue
            vendors_by_name[vendor_name] = {
                "key": _vendor_key_from_catalog(catalog_file),
                "inventory_names": [vendor_name],
                "schedule_name": None,
                "catalog_file": catalog_file,
                "catalog_type": "xlsx",
                "legacy": False,
            }

    # Discover vendors from POS inventory CSV (catalog optional — inventory maps the products)
    try:
        from app.dashboard.pos_data_service import _load_inventory_from_csv

        inventory = _load_inventory_from_csv()
        existing_files = {f.name.upper(): f.name for f in VENDORS_DIR.glob("*.xlsx")}
        for vendor_name in sorted(inventory["vendor_name"].dropna().astype(str).unique()):
            if vendor_name in vendors_by_name:
                continue
            if vendor_name.upper() in PRODUCE_VENDOR_NAMES or vendor_name == "Unknown":
                continue
            catalog_file = KNOWN_CATALOG_FILES.get(vendor_name) or _vendor_filename(vendor_name)
            has_catalog = catalog_file.upper() in existing_files
            if has_catalog:
                catalog_file = existing_files[catalog_file.upper()]
            else:
                catalog_file = ""
            vendors_by_name[vendor_name] = {
                "key": _vendor_key_from_catalog(catalog_file) if has_catalog else _vendor_key_from_name(vendor_name),
                "inventory_names": [vendor_name],
                "schedule_name": None,
                "catalog_file": catalog_file,
                "catalog_type": "xlsx" if has_catalog else "inventory_only",
                "legacy": False,
            }
    except Exception:
        pass

    ordered = sorted(vendors_by_name.values(), key=lambda v: v["inventory_names"][0])
    return tuple(ordered)


def _vendor_filename(vendor_name: str) -> str:
    return f"{_vendor_key_from_name(vendor_name)}.xlsx"


def get_vendor_meta_by_key(vendor_key: str) -> dict[str, Any] | None:
    for v in get_all_store_vendors():
        if v["key"] == vendor_key:
            return v
    return None


def get_vendor_meta_for_inventory_name(vendor_name: str) -> dict[str, Any]:
    for v in get_all_store_vendors():
        if vendor_name in v["inventory_names"]:
            return v
    return {}


def all_store_inventory_vendor_names() -> list[str]:
    names: list[str] = []
    for v in get_all_store_vendors():
        names.extend(v["inventory_names"])
    return names


def tracked_inventory_vendor_names() -> list[str]:
    """All store vendors with catalogs (replaces legacy 8-vendor list)."""
    return all_store_inventory_vendor_names()


DAY_MAP = {
    "MONDAY": 0,
    "MON": 0,
    "TUESDAY": 1,
    "TUE": 1,
    "WEDNESDAY": 2,
    "WED": 2,
    "THURSDAY": 3,
    "THU": 3,
    "THUR": 3,
    "FRIDAY": 4,
    "FRI": 4,
    "SATURDAY": 5,
    "SAT": 5,
    "SUNDAY": 6,
    "SUN": 6,
}


def _parse_pack_from_text(text: str) -> int | None:
    """Extract case pack count from strings like '12 x 50 gm', '20X200 GM', '100g x 12', or bare '24'."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        value = int(text)
        return value if value > 1 else None
    if not isinstance(text, str):
        text = str(text)
    t = text.strip()
    if not t or t.upper() in {"NAN", "NONE", "NULL"}:
        return None
    # Bare integer case qty from HOS "Case Qty" column
    if re.fullmatch(r"\d+", t):
        value = int(t)
        return value if value > 1 else None
    t = t.upper().replace(",", " ")
    m = re.search(r"(\d+)\s*[X×]\s*\d+", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\d+\s*[A-Z]+\s*[X×]\s*(\d+)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"^(\d+)\s*[X×]", t)
    if m:
        return int(m.group(1))
    return None


def schedule_is_known(order_cutoff: str | None, delivery_days: str | None) -> bool:
    """True when we have real cut-off and delivery days (not TBD / contact vendor)."""
    co = str(order_cutoff or "").strip().upper()
    dd = str(delivery_days or "").strip().upper()
    if co in _UNKNOWN_CUTOFF:
        return False
    if dd in _UNKNOWN_DELIVERY:
        return False
    return bool(co and dd)


def _schedule_row_to_dict(r: pd.Series) -> dict[str, Any]:
    return {
        "order_cutoff": str(r.get("order_cutoff", "")),
        "delivery_days": str(r.get("delivery_days", "")),
        "delivery_day_labels": str(r.get("delivery_day_labels", "")),
        "order_frequency": str(r.get("order_frequency", "")),
        "min_days_cover": str(r.get("min_days_cover", "")),
        "lead_time_days": int(r.get("lead_time_days", DEFAULT_NO_SCHEDULE_COVER_DAYS)),
        "has_known_schedule": schedule_is_known(r.get("order_cutoff"), r.get("delivery_days")),
    }


def _empty_schedule_fallback() -> dict[str, Any]:
    return {
        "order_cutoff": "No schedule — 14-day cover",
        "delivery_days": "TBD",
        "delivery_day_labels": "",
        "order_frequency": "Unknown",
        "min_days_cover": f"{DEFAULT_NO_SCHEDULE_COVER_DAYS} days",
        "lead_time_days": DEFAULT_NO_SCHEDULE_COVER_DAYS,
        "has_known_schedule": False,
    }


def lookup_vendor_schedule(
    inventory_vendor_name: str,
    schedule: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """
    Delivery schedule for any POS vendor_name.

    Uses tracked-vendor mapping first, then Excel row match.
    Falls back to 14-day reorder cover when cut-off/delivery unknown.
    """
    sched = schedule if schedule is not None else load_delivery_schedule()
    name = str(inventory_vendor_name or "").strip()

    for v in LEGACY_VENDORS:
        if name in v["inventory_names"]:
            out = get_vendor_schedule(v["key"], sched)
            out["has_known_schedule"] = schedule_is_known(out.get("order_cutoff"), out.get("delivery_days"))
            if not out["has_known_schedule"]:
                out["lead_time_days"] = DEFAULT_NO_SCHEDULE_COVER_DAYS
            return out

    if name:
        row = sched[sched["vendor_name"].str.upper() == name.upper()]
        if row.empty:
            # Prefer parenthetical alias first: UNITED TRADERS (GAZAB) → GAZAB
            paren = re.findall(r"\(([A-Z0-9][A-Z0-9 &/-]{1,})\)", name.upper())
            tokens = [p.strip() for p in paren] + sorted(
                re.findall(r"[A-Z0-9]{4,}", name.upper()),
                key=len,
                reverse=True,
            )
            seen: set[str] = set()
            for token in tokens:
                if token in seen or token in {"FOOD", "FOODS", "TRADER", "TRADERS", "PRODUCE"}:
                    continue
                seen.add(token)
                hit = sched[sched["vendor_name"].str.upper() == token]
                if hit.empty:
                    hit = sched[sched["vendor_name"].str.upper().str.contains(token, na=False, regex=False)]
                if not hit.empty:
                    row = hit
                    break
        if not row.empty:
            return _schedule_row_to_dict(row.iloc[0])

    return _empty_schedule_fallback()


def resolve_planning_cover_days(
    inventory_vendor_name: str,
    schedule: pd.DataFrame | None = None,
    *,
    explicit_cover_days: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Return (cover_days, schedule_dict) for reorder math."""
    sched = lookup_vendor_schedule(inventory_vendor_name, schedule)
    if explicit_cover_days is not None:
        return int(explicit_cover_days), sched
    if sched.get("has_known_schedule"):
        return int(sched.get("lead_time_days", DEFAULT_NO_SCHEDULE_COVER_DAYS)), sched
    return DEFAULT_NO_SCHEDULE_COVER_DAYS, sched


def _parse_delivery_days(text: str) -> list[str]:
    if not text or not isinstance(text, str):
        return []
    labels = []
    upper = text.upper()
    for token, idx in sorted(DAY_MAP.items(), key=lambda x: -len(x[0])):
        if token in upper and idx not in [DAY_MAP[l.upper()] for l in labels if l.upper() in DAY_MAP]:
            # map idx back to full name
            full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][idx]
            if full not in labels:
                labels.append(full)
    return labels


def _estimate_lead_days(cut_off: str, delivery: str) -> int:
    """Estimate lead time from cut-off day to first delivery day."""
    if not cut_off or not delivery:
        return 7
    if "MONTHLY" in str(cut_off).upper() or "MONTHLY" in str(delivery).upper():
        return 14
    co_days = _parse_delivery_days(str(cut_off))
    del_days = _parse_delivery_days(str(delivery))
    if not co_days or not del_days:
        return 5
    co_idx = DAY_MAP.get(co_days[0].upper()[:3], 0)
    del_idx = min(DAY_MAP.get(d.upper()[:3], d_idx) for d in del_days for d_idx in [DAY_MAP.get(d.upper()[:3], 0)])
    for d in del_days:
        del_idx = min(del_idx, DAY_MAP.get(d.upper()[:3], del_idx))
    lead = (del_idx - co_idx) % 7
    return max(lead, 2)


def load_delivery_schedule(path: Path | None = None, *, use_db: bool | None = None) -> pd.DataFrame:
    if path is not None:
        return _load_delivery_schedule_cached(str(path))
    if use_db is None:
        from database.readers.sandbox_data_reader import sandbox_db_available

        use_db = sandbox_db_available()
    if use_db:
        try:
            from database.readers.sandbox_data_reader import get_sandbox_reader

            df = get_sandbox_reader().load_delivery_schedule()
            if not df.empty:
                return df
        except Exception:
            pass
    return _load_delivery_schedule_cached(str(SCHEDULE_PATH))


@lru_cache(maxsize=2)
def _load_delivery_schedule_cached(path_str: str) -> pd.DataFrame:
    p = Path(path_str)
    df = pd.read_excel(p, sheet_name=0)
    df.columns = [str(c).strip().upper() for c in df.columns]
    rename = {
        "VENDOR NAME": "vendor_name",
        "CUT OFF WEEK TO FINAL": "order_cutoff",
        "DELIVERY DAYS": "delivery_days",
        "FREQUENCY OF ORDER": "order_frequency",
        "MIN QUANTITY MAINTAIN": "min_days_cover",
    }
    out = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    out["lead_time_days"] = out.apply(
        lambda r: _estimate_lead_days(str(r.get("order_cutoff", "")), str(r.get("delivery_days", ""))),
        axis=1,
    )
    out["delivery_day_labels"] = out["delivery_days"].apply(lambda x: ", ".join(_parse_delivery_days(str(x))))
    return out


def get_vendor_schedule(vendor_key: str, schedule: pd.DataFrame | None = None) -> dict[str, Any]:
    meta = get_vendor_meta_by_key(vendor_key)
    if not meta:
        return _empty_schedule_fallback()
    sched = schedule if schedule is not None else load_delivery_schedule()
    inventory_name = meta["inventory_names"][0]

    row = pd.DataFrame()
    if meta.get("schedule_name"):
        row = sched[sched["vendor_name"].str.upper() == str(meta["schedule_name"]).upper()]
    if row.empty:
        row = sched[sched["vendor_name"].str.upper() == inventory_name.upper()]
    if row.empty:
        token = inventory_name.split()[0]
        if len(token) >= 4:
            row = sched[sched["vendor_name"].str.upper().str.contains(token, na=False)]
    if row.empty:
        return _empty_schedule_fallback()
    r = row.iloc[0]
    out = _schedule_row_to_dict(r)
    if not out["has_known_schedule"]:
        out["lead_time_days"] = DEFAULT_NO_SCHEDULE_COVER_DAYS
        out["order_cutoff"] = str(r.get("order_cutoff", "")) or "No schedule — 14-day cover"
    return out


def _load_annapurna(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None)
    skip_exact = {
        "ITEM",
        "UNIT",
        "QTY",
        "ANNAPURNA FOODS",
        "COOLER PRODUCTS",
        "FROZEN PRODUCTS",
        "RAJBHOG",
        "ANAND",
        "BRAR'S",
        "BRARS",
        "ASHOKA",
        "RICE",
        "DECCAN",
        "KHAZANA",
        "BEDEKAR",
        "SPICES",
        "MDH",
        "SAKTHI",
        "ANAND PODI",
        "SUHANA",
        "ORCHID",
        "SNACKS",
        "ANAGANAGA",
        "ANAND MILLETS",
        "ANAND JAGGERY & LADDU",
        "ANAND FRYUMS",
        "GHEE",
        "TEA AND COFFEE",
        "SODAS",
        "PAPAD",
        "MISCH",
        "TAMARIND",
        "ECO DOSTH LEAF PLATES",
        "TENALI DOUBLE HORSE(TDH)",
    }
    rows = []
    for _, r in raw.iterrows():
        cells = [str(x).strip() if pd.notna(x) else "" for x in r.tolist()]
        name = cells[0] if cells else ""
        if not name:
            continue
        name_u = name.upper().strip()
        if name_u in skip_exact or name_u.endswith(" PRODUCTS"):
            continue
        if name_u == "ITEM":
            continue
        # Brand-only header rows (no unit / unit=UNIT)
        unit = cells[1] if len(cells) > 1 else ""
        if unit.upper() in {"", "UNIT", "NAN", "NONE"} and " X " not in name_u and "X" not in name_u[-6:]:
            # allow real products that omit unit column but look like items
            if len(name.split()) <= 2 and not any(ch.isdigit() for ch in name):
                continue
        if unit.upper() == "UNIT":
            continue
        pack = _parse_pack_from_text(name)
        rows.append(
            {
                "product_name": name,
                "unit": unit if unit and unit.upper() not in {"NAN", "NONE"} else "Case",
                "catalog_pack": pack,
            }
        )
    return pd.DataFrame(rows).drop_duplicates(subset=["product_name"])


def _load_hos(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    rows = []
    for _, r in df.iterrows():
        desc = str(r.get("Description", "")).strip()
        case = str(r.get("Case Qty", "")).strip()
        if not desc or desc == "Description":
            continue
        pack = _parse_pack_from_text(case) or _parse_pack_from_text(desc)
        rows.append({"product_name": desc, "unit": "Case", "catalog_pack": pack, "vendor_code": r.get("Code")})
    return pd.DataFrame(rows)


def _load_moghul(path: Path) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name="Product List ", header=None)
    rows = []
    for _, r in raw.iterrows():
        vals = [x for x in r.tolist() if pd.notna(x)]
        if len(vals) >= 2 and isinstance(vals[0], (int, float)) and not isinstance(vals[1], (int, float)):
            name = str(vals[1]).strip()
            pack_text = str(vals[2]).strip() if len(vals) > 2 else ""
            rows.append({"product_name": name, "unit": "Case", "catalog_pack": _parse_pack_from_text(pack_text)})
        if len(vals) >= 5 and isinstance(vals[3], (int, float)):
            name = str(vals[5]).strip() if len(vals) > 5 else ""
            if name:
                rows.append({"product_name": name, "unit": "Case", "catalog_pack": None})
    return pd.DataFrame(rows).drop_duplicates(subset=["product_name"])


def _load_premier(path: Path) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    if "Products" in xl.sheet_names:
        df = pd.read_excel(path, sheet_name="Products")
        out = df[["product_name", "unit", "catalog_pack"]].copy()
        if "brand" in df.columns:
            out["brand"] = df["brand"]
        if "vendor_code" in df.columns:
            out["vendor_code"] = df["vendor_code"]
        if "section" in df.columns:
            out["section"] = df["section"]
        return out.drop_duplicates(subset=["product_name"])

    df = pd.read_excel(path, sheet_name="Catalog", header=1)
    rows = []
    for _, r in df.iterrows():
        for brand_col, prod_col, uom_col in [("BRAND ", "PRODUCT", "UOM"), ("BRAND", "PRODUCT.1", "UOM.1")]:
            if prod_col not in df.columns:
                continue
            prod = r.get(prod_col)
            if pd.isna(prod) or str(prod).strip() == "":
                continue
            uom = str(r.get(uom_col, "")) if uom_col in df.columns else ""
            if "ALL /" in str(prod).upper() or str(prod).upper() == "NAN":
                continue
            rows.append(
                {
                    "product_name": str(prod).strip(),
                    "brand": str(r.get(brand_col, "")).strip() if brand_col in df.columns else "",
                    "unit": uom,
                    "catalog_pack": _parse_pack_from_text(uom),
                }
            )
    return pd.DataFrame(rows).drop_duplicates(subset=["product_name"])


def _load_tiranga(path: Path) -> pd.DataFrame:
    """Tiranga file is sparse; return empty and rely on inventory vendor filter."""
    try:
        raw = pd.read_excel(path, header=None)
        rows = []
        for _, r in raw.iterrows():
            vals = [str(x).strip() for x in r.tolist() if pd.notna(x) and str(x).strip()]
            if len(vals) >= 2 and len(vals[0]) > 3:
                rows.append({"product_name": vals[0], "unit": vals[1] if len(vals) > 1 else "Case", "catalog_pack": None})
        return pd.DataFrame(rows)
    except Exception:
        return pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])


def _load_extracted_catalog(path: Path) -> pd.DataFrame:
    """Load standardized Excel exported from PDF vendor catalogs."""
    df = pd.read_excel(path, sheet_name="Products")
    out = df[["product_name", "unit", "catalog_pack"]].copy()
    if "price" in df.columns:
        out["price"] = df["price"]
    if "section" in df.columns:
        out["section"] = df["section"]
    return out


def _ensure_pdf_catalog_xlsx(vendor_key: str, meta: dict[str, Any]) -> Path:
    """Build Excel catalog from PDF when missing."""
    xlsx_path = VENDORS_DIR / meta["catalog_file"]
    if xlsx_path.exists():
        return xlsx_path
    pdf_name = meta.get("pdf_source")
    if not pdf_name:
        return xlsx_path
    pdf_path = VENDORS_DIR / pdf_name
    if not pdf_path.exists():
        return xlsx_path
    import sys

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.extract_vendor_pdfs import extract_all_pdf_vendors

    extract_all_pdf_vendors()
    return xlsx_path


def _load_generic_products(path: Path) -> pd.DataFrame:
    """Standard Products sheet or inventory-exported vendor catalogs."""
    try:
        xl = pd.ExcelFile(path)
        sheet = "Products" if "Products" in xl.sheet_names else xl.sheet_names[0]
        df = pd.read_excel(path, sheet_name=sheet)
    except Exception:
        return pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])

    if "product_name" not in df.columns:
        if "description" in df.columns:
            df = df.rename(columns={"description": "product_name"})
        else:
            return pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])

    out = pd.DataFrame()
    out["product_name"] = df["product_name"].astype(str).str.strip()
    out["unit"] = df["unit"].astype(str).str.strip() if "unit" in df.columns else "Case"
    if "catalog_pack" in df.columns:
        out["catalog_pack"] = pd.to_numeric(df["catalog_pack"], errors="coerce")
    elif "pack" in df.columns:
        out["catalog_pack"] = pd.to_numeric(df["pack"], errors="coerce")
    else:
        out["catalog_pack"] = None
    return out.drop_duplicates(subset=["product_name"])


def load_vendor_catalog(vendor_key: str, *, use_db: bool | None = None) -> pd.DataFrame:
    """
    Load vendor order-form / catalog.

    Prefer the Excel catalog file when present — sandbox DB catalogs are often a
    truncated inventory dump (wrong packs) and miss order-form items like Anand Parotta.
    """
    file_df = _load_vendor_catalog_cached(vendor_key)
    if use_db is None:
        from database.readers.sandbox_data_reader import sandbox_db_available

        use_db = sandbox_db_available()
    if use_db and file_df.empty:
        try:
            from database.readers.sandbox_data_reader import get_sandbox_reader

            df = get_sandbox_reader().load_vendor_catalog(vendor_key)
            if not df.empty:
                return df
        except Exception:
            pass
    return file_df if not file_df.empty else pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])


def _load_vendor_catalog_impl(vendor_key: str) -> pd.DataFrame:
    meta = get_vendor_meta_by_key(vendor_key)
    if not meta:
        return pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])
    path = _ensure_pdf_catalog_xlsx(vendor_key, meta)
    if not path.exists():
        return pd.DataFrame(columns=["product_name", "unit", "catalog_pack"])
    loaders = {
        "ANNAPURNA": _load_annapurna,
        "EVEREST": _load_extracted_catalog,
        "HOS": _load_hos,
        "MOGHUL": _load_moghul,
        "OM": _load_extracted_catalog,
        "PREMIER": _load_premier,
        "SOHAM": _load_extracted_catalog,
        "TIRANGA": _load_tiranga,
    }
    loader = loaders.get(vendor_key, _load_generic_products)
    return loader(path)


@lru_cache(maxsize=128)
def _load_vendor_catalog_cached(vendor_key: str) -> pd.DataFrame:
    df = _load_vendor_catalog_impl(vendor_key)
    return df.copy()


def load_all_tracked_vendor_info() -> pd.DataFrame:
    """One row per store vendor with schedule + catalog count (no full catalog reload)."""
    schedule = load_delivery_schedule()
    cov_counts: dict[str, int] = {}
    if COVERAGE_PATH.exists():
        cov = pd.read_csv(COVERAGE_PATH)
        cov_counts = {
            str(r["vendor_name"]).strip(): int(r.get("catalog_products") or 0)
            for _, r in cov.iterrows()
        }

    rows = []
    for v in get_all_store_vendors():
        sched = get_vendor_schedule(v["key"], schedule)
        inv_name = v["inventory_names"][0]
        cat_count = cov_counts.get(inv_name, 0)
        rows.append(
            {
                "vendor_key": v["key"],
                "inventory_vendor_names": ", ".join(v["inventory_names"]),
                "catalog_products": cat_count,
                "catalog_file": v["catalog_file"],
                "has_catalog": cat_count > 0,
                "legacy_loader": bool(v.get("legacy")),
                **sched,
            }
        )
    return pd.DataFrame(rows)


load_all_store_vendor_info = load_all_tracked_vendor_info


def match_catalog_to_inventory(
    catalog: pd.DataFrame,
    inventory: pd.DataFrame,
    vendor_names: list[str],
    *,
    include_other_vendors: bool = False,
    min_score: float = 72.0,
) -> pd.DataFrame:
    """Match vendor catalog items to inventory rows using normalized names + fuzzy scoring.

    When include_other_vendors=True, match the primary POS vendor first, then only
    search other vendors for catalog rows still unmatched (much faster than fuzzy
    over the full store inventory).
    """
    primary = list(vendor_names)
    primary_set = set(primary)
    primary_inv = inventory[inventory["vendor_name"].isin(primary)].copy()
    if primary_inv.empty:
        upper = {n.upper().strip() for n in primary}
        primary_inv = inventory[
            inventory["vendor_name"].astype(str).str.upper().str.strip().isin(upper)
        ].copy()

    matched_primary = _match_catalog_to_inventory_core(
        catalog, primary_inv, min_score=min_score
    )

    if not include_other_vendors:
        return matched_primary.reset_index(drop=True)

    # Brand-token claims from other POS vendors (Anand under BABCO → Annapurna, etc.).
    # Do NOT fuzzy-match the unmatched catalog against other vendors —
    # Premier.xlsx alone is ~2000 lines × ~2600 other SKUs and multi-minute hangs the UI.
    other_inv = inventory[~inventory["vendor_name"].isin(primary_set)].copy()
    extra_parts: list[pd.DataFrame] = []

    brand_tokens = ORDER_FORM_BRAND_OWNERS.get(
        next(
            (v["key"] for v in LEGACY_VENDORS if set(v["inventory_names"]) & primary_set),
            "",
        ),
        (),
    )
    if not brand_tokens:
        for key, toks in ORDER_FORM_BRAND_OWNERS.items():
            meta = get_vendor_meta_by_key(key)
            if meta and set(meta["inventory_names"]) & primary_set:
                brand_tokens = toks
                break

    already_upcs = set(matched_primary["upc"].astype(str))

    if brand_tokens and not other_inv.empty:
        desc_u = other_inv["description"].astype(str).str.upper()
        brand_hit = pd.Series(False, index=other_inv.index)
        for tok in brand_tokens:
            brand_hit = brand_hit | desc_u.str.contains(
                rf"(?:^|[\s]){re.escape(tok)}(?:[\s]|$)", regex=True, na=False
            )
        for fp in BRAND_FALSE_POSITIVES:
            brand_hit = brand_hit & ~desc_u.str.contains(fp, na=False)
        brand_rows = other_inv.loc[brand_hit & ~other_inv["upc"].astype(str).isin(already_upcs)].copy()
        if not brand_rows.empty:
            brand_rows["catalog_matched"] = True
            brand_rows["catalog_match_score"] = float(min_score)
            brand_rows["catalog_product_name"] = brand_rows["description"].astype(str)
            brand_rows["catalog_pack"] = pd.to_numeric(brand_rows.get("pack"), errors="coerce")
            extra_parts.append(brand_rows)

    frames = [matched_primary] + extra_parts
    inv = pd.concat(frames, ignore_index=True, sort=False) if extra_parts else matched_primary
    inv = inv.drop_duplicates(subset=["upc"], keep="first")
    inv["pos_vendor_name"] = inv["vendor_name"]
    inv.loc[~inv["vendor_name"].isin(primary_set), "vendor_name"] = primary[0]
    return inv.reset_index(drop=True)


def _match_catalog_to_inventory_core(
    catalog: pd.DataFrame,
    inv: pd.DataFrame,
    *,
    min_score: float = 72.0,
) -> pd.DataFrame:
    """Core UPC / name / fuzzy matcher against a (usually smaller) inventory slice."""
    inv = inv.copy()
    if catalog.empty or inv.empty:
        inv["catalog_matched"] = False
        inv["catalog_match_score"] = 0.0
        inv["catalog_product_name"] = ""
        inv["catalog_pack"] = pd.to_numeric(inv.get("pack"), errors="coerce")
        return inv

    size_series = inv["size"] if "size" in inv.columns else pd.Series("", index=inv.index)
    inv["norm_desc"] = [
        norm_name(str(d), size_field=str(s or "") or None)
        for d, s in zip(inv["description"].astype(str), size_series.astype(str))
    ]
    catalog = catalog.copy()
    catalog["norm_name"] = catalog["product_name"].map(lambda x: norm_name(str(x)))

    matched_keys: set[str] = set()
    match_scores: dict[str, float] = {}
    match_names: dict[str, str] = {}

    inv_upc = inv["upc"].astype(str).str.strip()

    # 1) UPC exact match (vectorized)
    if "upc" in catalog.columns:
        cat_upc = catalog.copy()
        cat_upc["upc"] = cat_upc["upc"].astype(str).str.strip()
        upc_to_name = (
            cat_upc.drop_duplicates(subset=["upc"])
            .set_index("upc")["product_name"]
            .astype(str)
            .to_dict()
        )
        for upc, name in ((u, upc_to_name[u]) for u in inv_upc if u in upc_to_name):
            matched_keys.add(upc)
            match_scores[upc] = 100.0
            match_names[upc] = name

    # 2) Normalized name exact match (vectorized)
    norm_to_name = dict(zip(catalog["norm_name"], catalog["product_name"].astype(str)))
    for upc, nd in zip(inv_upc, inv["norm_desc"]):
        if upc in matched_keys:
            continue
        if nd and nd in norm_to_name:
            matched_keys.add(upc)
            match_scores[upc] = 100.0
            match_names[upc] = norm_to_name[nd]

    catalog_names = catalog["product_name"].astype(str).tolist()

    # 3) Substring pass for still-unmatched (primary-sized set is small)
    for _, cat in catalog.iterrows():
        cn = str(cat["norm_name"])
        cat_name = str(cat["product_name"])
        if len(cn) < 4:
            continue
        unmatched = inv[~inv_upc.isin(matched_keys)]
        if unmatched.empty:
            break
        needle = cn[: min(len(cn), 12)]
        hits = unmatched[unmatched["norm_desc"].str.contains(needle, na=False, regex=False)]
        if hits.empty and len(cn) >= 8:
            hits = unmatched[unmatched["norm_desc"].map(lambda d: cn in d or d in cn)]
        for _, hit in hits.iterrows():
            upc = str(hit["upc"]).strip()
            sc = match_score(
                str(hit["description"]),
                cat_name,
                size_a=str(hit.get("size", "") or "") or None,
            )
            if sc < min_score:
                continue
            matched_keys.add(upc)
            match_scores[upc] = max(match_scores.get(upc, 0), sc)
            match_names[upc] = cat_name

    # 4) Fuzzy match only for remaining rows — token-index candidate filter
    from collections import defaultdict

    token_index: dict[str, list[int]] = defaultdict(list)
    for i, cn in enumerate(catalog["norm_name"].astype(str)):
        for tok in cn.split()[:5]:
            if len(tok) >= 3:
                token_index[tok].append(i)

    unmatched = inv[~inv_upc.isin(matched_keys)]
    for _, row in unmatched.iterrows():
        upc = str(row["upc"]).strip()
        inv_desc = str(row.get("description", ""))
        inv_size = str(row.get("size", "") or "") or None
        sig = product_signature(inv_desc, size_field=inv_size)
        candidate_idx: set[int] = set()
        for tok in sig["core_tokens"][:4]:
            candidate_idx.update(token_index.get(tok, []))
        if not candidate_idx:
            prefix = (sig["norm_name"] or inv_desc.upper())[:4]
            if prefix:
                for i, cn in enumerate(catalog["norm_name"].astype(str)):
                    if cn.startswith(prefix):
                        candidate_idx.add(i)
        if not candidate_idx:
            continue
        candidates = [catalog_names[i] for i in candidate_idx if i < len(catalog_names)]
        best_name, best = best_catalog_match(
            inv_desc, candidates, inv_size=inv_size, min_score=min_score
        )
        if best_name:
            matched_keys.add(upc)
            match_scores[upc] = best
            match_names[upc] = best_name

    inv["catalog_matched"] = inv_upc.isin(matched_keys)
    inv["catalog_match_score"] = inv_upc.map(lambda u: float(match_scores.get(u, 0.0)))
    inv["catalog_product_name"] = inv_upc.map(lambda u: match_names.get(u, ""))

    cat_pack_by_name: dict[str, float] = {}
    if "catalog_pack" in catalog.columns:
        for _, c in catalog.iterrows():
            p = c.get("catalog_pack")
            if pd.notna(p) and float(p) > 0:
                cat_pack_by_name[str(c["product_name"])] = float(p)

    pos_pack = pd.to_numeric(inv.get("pack"), errors="coerce")
    inv["catalog_pack"] = [
        cat_pack_by_name[c] if c in cat_pack_by_name else p
        for c, p in zip(inv["catalog_product_name"].astype(str), pos_pack)
    ]
    if "pack" in inv.columns:
        inv["pack"] = [
            int(cp) if pd.notna(cp) and float(cp) > 1 and matched else (p if pd.notna(p) else 1)
            for cp, p, matched in zip(
                inv["catalog_pack"],
                pos_pack,
                inv["catalog_matched"],
            )
        ]

    return inv
