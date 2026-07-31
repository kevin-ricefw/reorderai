"""
Parse Swadesh past-invoice workbook → latest ordered qty per SKU.

Used when Paul ``vendor_order_products`` is empty/stub so detect-order can still
surface ``last_pallet_qty`` from real invoices under ``data/Past Invoices/``.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from config.data_paths import CACHE_DIR, INVENTORY_DIR, PAST_INVOICES_DIR
from v2.forecasting.local_pos_sales import normalize_upc

_WORKBOOK_NAME = "SWADESH INVOICE LIST.xlsx"
_CACHE_NAME = "past_invoices_latest.json"

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "aprl": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_VENDOR_ALIASES = {
    "HOS": "HOS",
    "LAXMI": "HOS",
    "OM PRODUCE": "OM PRODUCE",
    "OM": "OM PRODUCE",
    "PREMIER": "PREMIER",
    "CHETAK": "CHETAK",
    "JALARAM": "JALARAM",
    "DEEP": "DEEP",
    "EVEREST": "EVEREST",
    "RAJA": "RAJA",
    "TRINITY": "TRINITY",
}

_COL_ALIASES = {
    "qty": {
        "QTY GIVEN NUM",
        "QTY GIVENNUM",
        "QTY GIVEN",
        "QTY",
        "QUANTITY",
        "CASES",
    },
    "case_count": {"CASE COUNT", "CASECOUNT", "PACK", "PACK SIZE"},
    "description": {"DESCRIPTION", "NAME IN POS", "ITEM", "PRODUCT"},
    "code": {"CODE", "ITEM CODE", "VENDOR CODE", "SKU"},
    "upc": {"UPC CODE", "UPC", "BARCODE", "EAN"},
    "vendor_name": {"VENDOR NAME", "VENDOR"},
}

_BARCODE_IN_TEXT = re.compile(r"(\d{8,14})")
_PACK_IN_DESC = re.compile(r"(\d+)\s*[xX×]\s*\d+", re.IGNORECASE)


def _norm_col(name: object) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().upper())


def _pick_col(columns: list[str], aliases: set[str]) -> str | None:
    for c in columns:
        if _norm_col(c) in aliases:
            return c
    # soft contains
    for c in columns:
        nc = _norm_col(c)
        for a in aliases:
            if a in nc or nc in a:
                return c
    return None


def _parse_sheet_vendor_and_date(sheet: str) -> tuple[str | None, date | None]:
    raw = str(sheet or "").strip()
    upper = raw.upper()
    vendor_key: str | None = None
    # longest alias first
    for alias in sorted(_VENDOR_ALIASES.keys(), key=len, reverse=True):
        if upper.startswith(alias) or f" {alias} " in f" {upper} ":
            vendor_key = _VENDOR_ALIASES[alias]
            break
    if vendor_key is None:
        # first token fallback
        token = re.split(r"[\s_]+", upper)[0]
        vendor_key = _VENDOR_ALIASES.get(token, token if token else None)

    # date tokens: 22 JAN 2026 / 31 JAN 26 / 02 APRIL 26
    m = re.search(
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{2,4})",
        raw,
    )
    inv_date: date | None = None
    if m:
        day = int(m.group(1))
        month = _MONTHS.get(m.group(2).lower())
        year = int(m.group(3))
        if year < 100:
            year += 2000
        if month:
            try:
                inv_date = date(year, month, day)
            except ValueError:
                inv_date = None
    return vendor_key, inv_date


def _to_float(val: object) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().replace(",", "")
    if not s or s.lower() in {"nan", "none", "-"}:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _extract_upc_candidates(raw: object) -> list[str]:
    text = str(raw or "")
    out: list[str] = []
    for m in _BARCODE_IN_TEXT.findall(text):
        n = normalize_upc(m)
        if n and n not in out:
            out.append(n)
    # also short HOS-style codes like 80007
    digits = re.sub(r"\D", "", text)
    if digits:
        n = normalize_upc(digits)
        if n and n not in out:
            out.append(n)
    return out


def _pack_from_description(desc: object) -> float | None:
    m = _PACK_IN_DESC.search(str(desc or ""))
    if not m:
        return None
    try:
        n = float(m.group(1))
        return n if n > 0 else None
    except ValueError:
        return None


def _norm_name(text: object) -> str:
    s = re.sub(r"[^A-Z0-9]+", " ", str(text or "").upper())
    return re.sub(r"\s+", " ", s).strip()


def _workbook_path() -> Path | None:
    p = PAST_INVOICES_DIR / _WORKBOOK_NAME
    return p if p.exists() else None


def _cache_path() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / _CACHE_NAME


def _parse_workbook(path: Path) -> list[dict[str, Any]]:
    xl = pd.ExcelFile(path)
    rows: list[dict[str, Any]] = []
    for sheet in xl.sheet_names:
        vendor_key, inv_date = _parse_sheet_vendor_and_date(sheet)
        if not vendor_key:
            continue
        try:
            df = pd.read_excel(xl, sheet_name=sheet, dtype=object)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        cols = list(df.columns)
        c_qty = _pick_col(cols, _COL_ALIASES["qty"])
        if not c_qty:
            continue
        c_case = _pick_col(cols, _COL_ALIASES["case_count"])
        c_desc = _pick_col(cols, _COL_ALIASES["description"])
        c_code = _pick_col(cols, _COL_ALIASES["code"])
        c_upc = _pick_col(cols, _COL_ALIASES["upc"])
        c_vname = _pick_col(cols, _COL_ALIASES["vendor_name"])

        for _, r in df.iterrows():
            qty_cases = _to_float(r.get(c_qty))
            if qty_cases is None or qty_cases <= 0:
                continue
            desc = str(r.get(c_desc) or "").strip() if c_desc else ""
            code = str(r.get(c_code) or "").strip().upper() if c_code else ""
            if code in {"NAN", "NONE", "NAT"}:
                code = ""
            case_count = _to_float(r.get(c_case)) if c_case else None
            if case_count is None or case_count <= 0:
                case_count = _pack_from_description(desc)

            upc_cands: list[str] = []
            if c_upc:
                upc_cands.extend(_extract_upc_candidates(r.get(c_upc)))
            if c_vname:
                upc_cands.extend(_extract_upc_candidates(r.get(c_vname)))
            # de-dupe
            seen: set[str] = set()
            upcs: list[str] = []
            for u in upc_cands:
                if u not in seen:
                    seen.add(u)
                    upcs.append(u)

            units = qty_cases * float(case_count) if case_count and case_count > 0 else qty_cases
            rows.append(
                {
                    "vendor_key": vendor_key,
                    "invoice_date": inv_date.isoformat() if inv_date else None,
                    "sheet": sheet,
                    "code": code,
                    "upcs": upcs,
                    "description": desc,
                    "name_key": _norm_name(desc),
                    "qty_cases": float(qty_cases),
                    "case_count": float(case_count) if case_count else None,
                    "qty_units": float(units),
                }
            )
    return rows


def _latest_by_keys(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse to latest line per vendor+match-key."""
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        vk = row["vendor_key"]
        keys: list[str] = []
        if row.get("code"):
            keys.append(f"code:{row['code']}")
        for u in row.get("upcs") or []:
            keys.append(f"upc:{u}")
        nk = row.get("name_key") or ""
        if nk:
            keys.append(f"name:{nk}")
        if not keys:
            continue
        d = row.get("invoice_date") or ""
        for k in keys:
            full = f"{vk}|{k}"
            prev = best.get(full)
            if prev is None or (d and d >= (prev.get("invoice_date") or "")):
                best[full] = row
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "line_count": len(rows),
        "index": {
            k: {
                "vendor_key": v["vendor_key"],
                "invoice_date": v.get("invoice_date"),
                "code": v.get("code"),
                "upcs": v.get("upcs"),
                "description": v.get("description"),
                "name_key": v.get("name_key"),
                "qty_cases": v.get("qty_cases"),
                "case_count": v.get("case_count"),
                "qty_units": v.get("qty_units"),
                "sheet": v.get("sheet"),
            }
            for k, v in best.items()
        },
    }


def build_invoice_cache(*, force: bool = False) -> dict[str, Any]:
    path = _workbook_path()
    cache = _cache_path()
    if path is None:
        return {"generated_at": None, "line_count": 0, "index": {}}
    if (
        not force
        and cache.exists()
        and cache.stat().st_mtime >= path.stat().st_mtime
    ):
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    rows = _parse_workbook(path)
    payload = _latest_by_keys(rows)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    load_latest_invoice_lines.cache_clear()
    return payload


@lru_cache(maxsize=1)
def load_latest_invoice_lines() -> dict[str, Any]:
    return build_invoice_cache(force=False)


@lru_cache(maxsize=1)
def _local_product_attrs() -> dict[str, dict[str, Any]]:
    """UPC → {cert_code, pack} from local products.csv."""
    path = INVENTORY_DIR / "products.csv"
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:
        return {}
    cols = {c.lower(): c for c in df.columns}
    upc_c = cols.get("upc")
    if not upc_c:
        return {}
    cert_c = cols.get("cert_code") or cols.get("certcode")
    pack_c = cols.get("pack") or cols.get("reorderquantity")
    out: dict[str, dict[str, Any]] = {}
    for _, r in df.iterrows():
        upc = normalize_upc(r.get(upc_c))
        if not upc:
            continue
        cert = str(r.get(cert_c) or "").strip().upper() if cert_c else ""
        if cert in {"NAN", "NONE"}:
            cert = ""
        pack = _to_float(r.get(pack_c)) if pack_c else None
        out[upc] = {"cert_code": cert or None, "pack": pack}
    return out


def vendor_key_from_name(vendor_name: str | None) -> str | None:
    if not vendor_name:
        return None
    upper = str(vendor_name).upper()
    for alias in sorted(_VENDOR_ALIASES.keys(), key=len, reverse=True):
        if alias in upper:
            return _VENDOR_ALIASES[alias]
    return None


def _upc_match(item_upc: str | None, inv_upcs: list[str]) -> bool:
    iu = normalize_upc(item_upc or "")
    if not iu:
        return False
    for u in inv_upcs:
        if not u:
            continue
        if iu == u or iu.endswith(u) or u.endswith(iu):
            # avoid ultra-short accidental matches
            if min(len(iu), len(u)) >= 4:
                return True
    return False


def last_pallet_qty_for_items(
    items: list[dict[str, Any]],
    *,
    vendor_name: str | None,
    prefer_units: bool = True,
) -> dict[str, float]:
    """
    Map item_id → last invoice qty (units by default).

    Match priority per item: vendor code/sku/cert → UPC → normalized name.
    """
    vk = vendor_key_from_name(vendor_name)
    if not vk:
        return {}
    payload = load_latest_invoice_lines()
    index: dict[str, Any] = payload.get("index") or {}
    if not index:
        return {}

    # Pre-index this vendor's lines for faster name scan
    vendor_lines = [v for k, v in index.items() if k.startswith(f"{vk}|")]
    out: dict[str, float] = {}

    local_attrs = _local_product_attrs()

    for it in items:
        iid = str(it.get("item_id") or "")
        if not iid:
            continue
        hit: dict[str, Any] | None = None
        item_upc_n = normalize_upc(it.get("upc") or "")
        attrs = local_attrs.get(item_upc_n) or {}

        code_cands = []
        for key in ("sku", "vendor_code", "cert_code", "code"):
            val = str(it.get(key) or "").strip().upper()
            if val and val not in code_cands:
                code_cands.append(val)
        local_cert = attrs.get("cert_code")
        if local_cert and local_cert not in code_cands:
            code_cands.append(str(local_cert))
        for code in code_cands:
            row = index.get(f"{vk}|code:{code}")
            if row:
                hit = row
                break

        if hit is None:
            upc = it.get("upc")
            for line in vendor_lines:
                if _upc_match(upc, list(line.get("upcs") or [])):
                    hit = line
                    break

        if hit is None:
            nk = _norm_name(it.get("description") or it.get("name") or "")
            if nk:
                row = index.get(f"{vk}|name:{nk}")
                if row:
                    hit = row
                else:
                    # soft: invoice name contained in item or vice versa (len>=12)
                    for line in vendor_lines:
                        ink = line.get("name_key") or ""
                        if len(ink) < 12:
                            continue
                        if ink in nk or nk in ink:
                            hit = line
                            break

        if not hit:
            continue
        if prefer_units:
            if hit.get("case_count"):
                qty = hit.get("qty_units")
            else:
                pack = attrs.get("pack") or it.get("box_qty")
                cases = hit.get("qty_cases")
                if cases is not None and pack and float(pack) > 0:
                    qty = float(cases) * float(pack)
                else:
                    qty = hit.get("qty_units") or cases
        else:
            qty = hit.get("qty_cases")
        if qty is not None:
            out[iid] = float(qty)
    return out
