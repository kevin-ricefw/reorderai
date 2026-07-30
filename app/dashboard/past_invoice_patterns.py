"""Learn ordering / shelf patterns from past vendor invoices (manual back-team orders)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAST_INVOICES_DIR = PROJECT_ROOT / "data" / "Past Invoices"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
LINES_CACHE = CACHE_DIR / "past_invoice_lines.parquet"
PATTERNS_CACHE = CACHE_DIR / "past_invoice_patterns.json"
SIG_CACHE = CACHE_DIR / "past_invoice_source_sig.txt"
PATTERNS_CSV = PROJECT_ROOT / "outputs" / "analytics" / "invoice_order_patterns.csv"
# Bump when parse logic changes so disk cache rebuilds even if Excel mtime is unchanged
# Bump when parse logic changes so disk cache rebuilds even if Excel mtime is unchanged
_CACHE_LOGIC_VERSION = "invoice-cases-as-cases-v4"

# Header aliases seen across invoice sheets
_HEADER_MAP = {
    "s.no": "sno",
    "s no": "sno",
    "vendor name": "vendor_name",
    "vendior name": "vendor_name",
    "vendor": "vendor_name",
    "description": "description",
    "item code": "item_code",
    "brand": "brand",
    "unit size": "unit_size",
    "case cost": "case_cost",
    "case count": "case_count",
    "casec count": "case_count",
    "current cost": "unit_cost",
    "price": "price",
    "qty given num": "qty_ordered",
    "qty given": "qty_ordered",
    "qty givennum": "qty_ordered",
    "qtygiven num": "qty_ordered",
    "qty givwn num": "qty_ordered",
    "qty givenum": "qty_ordered",
    "qty give num": "qty_ordered",
    "quantity given num": "qty_ordered",
    # Partner qty column (when present) — NOT Swadesh order qty
    "kisan": "qty_partner",
    "qty on hand": "qty_on_hand",
    "qty onhand": "qty_on_hand",
    "expiration": "expiration",
    "name in pos": "pos_name",
    "upc code": "upc",
    "upc": "upc",
    "remarks": "remarks",
}

# Rows tagged for the other store — do not train as Swadesh demand
_PARTNER_ROW_RE = re.compile(
    r"^\s*kisan\s*$|sharing\s*kisan|not\s*received\s*kisan|kisan\s*vallu",
    re.IGNORECASE,
)

_OURS_QTY_RE = re.compile(
    r"(?:ours?|swadesh)\s*[=:]?\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(?:ours?|swadesh)",
    re.IGNORECASE,
)


def _norm_header(val: Any) -> str:
    s = str(val or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _norm_name(val: Any) -> str:
    s = str(val or "").upper().strip()
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_upc(val: Any) -> str:
    s = str(val or "").strip()
    if not s or s.lower() in {"nan", "none", "ok"}:
        return ""
    # Premier sheets sometimes embed UPC in vendor cell like "[image]\n8904..."
    m = re.search(r"(\d{8,14})", s)
    if m:
        return m.group(1)
    s = re.sub(r"\.0$", "", s)
    return s if s.isdigit() else ""


def _map_header(val: Any, col_idx: int) -> str:
    n = _norm_header(val)
    if not n or n == "nan":
        return f"col_{col_idx}"
    if n in _HEADER_MAP:
        return _HEADER_MAP[n]
    # Fuzzy: description often embeds invoice meta
    if n.startswith("description"):
        return "description"
    if "qty" in n and "given" in n:
        return "qty_ordered"
    if "qty" in n and "hand" in n:
        return "qty_on_hand"
    # "SWADESH" / "OURS" alone is ambiguous — title stamp vs qty column.
    # Resolve later in _finalize_headers_with_body().
    if n in {"swadesh", "our", "ours"} or n.startswith("ours"):
        return "swadesh_or_ours_raw"
    if n == "kisan":
        return "qty_partner"
    if "case count" in n or n in {"casec count", "casse count"}:
        return "case_count"
    if "upc" in n:
        return "upc"
    # Product names sometimes sit under an invoice-title header (no "description")
    if "jalaram" in n or "invoice" in n or re.search(r"\bd\s*\d{1,2}[/-]", n):
        return f"maybe_desc_{col_idx}"
    return f"col_{col_idx}"


def _finalize_headers_with_body(clean_headers: list[str], body: pd.DataFrame) -> list[str]:
    """
    Disambiguate messy headers using the actual cell values.

    - 'SWADESH' as col0 with almost no numbers = store title stamp (ignore)
    - 'SWADESH' with numeric values = this store's qty column
    - maybe_desc_* with long text = product description
    """
    headers = list(clean_headers)
    if body is None or body.empty:
        return headers

    for i, h in enumerate(headers):
        if not str(h).startswith("swadesh_or_ours_raw"):
            continue
        col = body.iloc[:, i] if i < body.shape[1] else pd.Series(dtype=object)
        nums = sum(1 for v in col if _to_float(v) is not None)
        filled = sum(1 for v in col if pd.notna(v) and str(v).strip() and str(v).lower() != "nan")
        # Numeric SWADESH column = qty; empty/title stamp = ignore
        headers[i] = "qty_ours" if nums >= max(3, int(0.3 * max(filled, 1))) else "store_stamp"

    # Promote maybe_desc to description if we don't already have one
    if "description" not in headers:
        for i, h in enumerate(headers):
            if not str(h).startswith("maybe_desc"):
                continue
            col = body.iloc[:, i] if i < body.shape[1] else pd.Series(dtype=object)
            textish = 0
            for v in col.head(20):
                if pd.isna(v):
                    continue
                s = str(v).strip()
                if len(s) >= 4 and not s.replace(".", "", 1).isdigit():
                    textish += 1
            if textish >= 3:
                headers[i] = "description"
                break
    return headers


def _row_is_partner_store(row: pd.Series) -> bool:
    """True when back-team marked this line as Kisan / other store — skip training."""
    for val in row.tolist():
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        s = str(val).strip()
        if not s or len(s) > 60:
            continue
        if _PARTNER_ROW_RE.search(s):
            return True
        if s.upper() == "KISAN":
            return True
    return False


def _to_float(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "ok", "-"}:
        return None
    # "15.00/16.00" → first number
    if "/" in s:
        s = s.split("/")[0]
    s = s.replace("$", "").replace(",", "").strip()
    # "8PC" / "6 pcs" / "7LB" on shared sheets
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:pc|pcs|pk|pack|lb|lbs)?\.?$", s, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def _extract_ours_qty_from_text(*vals: Any) -> float | None:
    """Parse notes like 'ours 8', 'ours:6', '8 ours', 'swadesh 4'."""
    for val in vals:
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        t = str(val).strip()
        if not t or len(t) > 80:
            continue
        m = _OURS_QTY_RE.search(t)
        if not m:
            continue
        num = m.group(1) or m.group(2)
        try:
            q = float(num)
        except ValueError:
            continue
        if q > 0:
            return q
    return None


def _sheet_looks_shared(sheet: str) -> bool:
    s = str(sheet or "").upper()
    return bool(re.search(r"\bK-S\b|\bS-K\b|KISAN", s))


def _parse_qty_cell(val: Any) -> tuple[float | None, str]:
    """
    Parse a qty cell.

    Returns (number, kind) where kind is 'cases' | 'pieces' | 'unknown'.
    Shared sheets sometimes put Swadesh share as '8PC' (pieces), not cases.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None, "unknown"
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "ok", "-"}:
        return None, "unknown"
    kind = "cases"
    # "8PC" has no word-boundary before PC — match pc/pcs anywhere
    if re.search(r"(?<![a-z])pc(?:s)?(?![a-z])|pieces?", s, re.IGNORECASE):
        kind = "pieces"
    num = _to_float(s)
    if num is None or num <= 0:
        return None, "unknown"
    return num, kind


def _resolve_ours_cases(row: pd.Series, sheet: str) -> tuple[float | None, float | None, str, str]:
    """
    Best-effort THIS-store qty from messy shared invoices.

    Reality of this Excel (from inspection):
    - Most sheets: only one QTY GIVEN — may be Swadesh-only OR full shared pallet
      (back team did not always label which). We keep it, flagged low-confidence.
    - K-S sheets with TWO qty columns: second usually ≤ first (Carloes) → use second.
    - Explicit SWADESH numeric column: use that.
    - KISAN column beside QTY GIVEN: QTY GIVEN is usually Swadesh share already.
    """
    ours_col, ours_kind = _parse_qty_cell(row.get("qty_ours"))
    partner, _ = _parse_qty_cell(row.get("qty_partner"))
    qty0, kind0 = _parse_qty_cell(row.get("qty_ordered"))
    qty1, kind1 = _parse_qty_cell(row.get("qty_ordered_1"))

    note_ours = _extract_ours_qty_from_text(
        row.get("remarks"),
        row.get("qty_on_hand"),
        *(row.get(c) for c in row.index if str(c).startswith("col_")),
    )

    if ours_col is not None:
        total = qty0 if qty0 and qty0 >= ours_col else None
        return ours_col, total, "swadesh_col", ours_kind or "cases"

    if note_ours is not None:
        total = qty0 if qty0 and qty0 >= note_ours else (qty1 if qty1 and qty1 >= note_ours else None)
        return note_ours, total, "ours_note", "cases"

    # Two QTY GIVEN columns — only trust split on clearly shared sheet tabs
    if qty0 is not None and qty1 is not None and _sheet_looks_shared(sheet):
        if kind1 == "pieces":
            return qty1, qty0, "shared_pieces", "pieces"
        if qty1 <= qty0:
            return qty1, qty0, "shared_second_qty", "cases"
        return qty0, qty1, "shared_first_qty", "cases"

    if qty0 is not None:
        # KISAN col present: qty_given is typically already Swadesh's portion
        if partner is not None and partner > 0:
            return qty0, (qty0 + partner), "qty_given_with_kisan_col", kind0 or "cases"
        return qty0, None, "qty_given", kind0 or "cases"

    if qty1 is not None:
        return qty1, None, "qty_given_alt", kind1 or "cases"

    return None, None, "", "cases"


def _parse_sheet_name(sheet: str) -> tuple[str, str]:
    """Best-effort vendor + date label from sheet tab name."""
    name = str(sheet or "").strip()
    return name, name


def _find_header_row(raw: pd.DataFrame) -> int | None:
    for i in range(min(8, len(raw))):
        cells = [_norm_header(c) for c in raw.iloc[i].tolist()]
        joined = " | ".join(cells)
        if "description" in joined and (
            "qty given" in joined
            or "case count" in joined
            or "swadesh" in joined
            or "ours" in joined
        ):
            return i
        # Title-stamp sheets: SWADESH | <invoice title> | case cost | qty given
        if "qty given" in joined and ("case cost" in joined or "case count" in joined):
            return i
    return None

def _infer_pack_from_description(desc: str) -> float | None:
    """
    Infer units-per-case from invoice description when CASE COUNT is blank.

    Examples: 'AMUL MILK - GOLD 6% FAT 4/1 GAL' → 4; '12/680 GM' → 12.
    """
    s = str(desc or "")
    m = re.search(r"(\d+)\s*[xX/]\s*1\s*(?:gal|gallon)s?\b", s, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r"\b(\d+)\s*[xX/]\s*\d+", s)
    if m:
        n = float(m.group(1))
        if 2 <= n <= 120:
            return n
    return None


def _repair_case_units(lines: pd.DataFrame) -> pd.DataFrame:
    """
    QTY GIVEN is cases. Never treat cases as units when CASE COUNT is missing.

    Backfill pack size from description / same-product history, then
    units = cases × units_per_case.
    """
    if lines is None or lines.empty:
        return lines
    df = lines.copy()
    if "case_count" not in df.columns:
        df["case_count"] = None
    if "cases_ordered" not in df.columns:
        return df

    miss = df["case_count"].isna() | (pd.to_numeric(df["case_count"], errors="coerce").fillna(0) <= 0)
    if miss.any() and "description" in df.columns:
        df.loc[miss, "case_count"] = df.loc[miss, "description"].map(_infer_pack_from_description)

    # Same product: fill remaining blanks from the usual case count on other invoices
    still = df["case_count"].isna() | (pd.to_numeric(df["case_count"], errors="coerce").fillna(0) <= 0)
    if still.any() and "norm_desc" in df.columns:
        known = df[pd.to_numeric(df["case_count"], errors="coerce").fillna(0) > 1]
        if not known.empty:
            mode_by = known.groupby("norm_desc")["case_count"].agg(
                lambda s: float(pd.Series(s).mode().iloc[0]) if len(s) else None
            )
            df.loc[still, "case_count"] = df.loc[still, "norm_desc"].map(mode_by)

    packs = pd.to_numeric(df["case_count"], errors="coerce")
    cases = pd.to_numeric(df["cases_ordered"], errors="coerce")
    has_pack = packs.notna() & (packs > 0) & cases.notna() & (cases > 0)
    df.loc[has_pack, "units_ordered"] = (cases[has_pack] * packs[has_pack]).astype(float)
    # Rows still without pack: keep cases only — do NOT pretend cases are units
    no_pack = ~has_pack & cases.notna() & (cases > 0)
    df.loc[no_pack, "units_ordered"] = pd.NA
    return df


def _invoice_xlsx_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.glob("*.xlsx") if not p.name.startswith("~$")
    )


def _source_signature(folder: Path) -> str:
    parts: list[str] = [_CACHE_LOGIC_VERSION]
    for p in _invoice_xlsx_files(folder):
        st = p.stat()
        parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(parts)


def _cache_fresh(folder: Path) -> bool:
    if not (LINES_CACHE.exists() and PATTERNS_CACHE.exists() and SIG_CACHE.exists()):
        return False
    try:
        return SIG_CACHE.read_text(encoding="utf-8").strip() == _source_signature(folder)
    except OSError:
        return False


def _dedupe_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            out.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            out.append(h)
    return out


def _load_one_sheet_from_frame(raw: pd.DataFrame, sheet: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    hdr_i = _find_header_row(raw)
    if hdr_i is None:
        return pd.DataFrame()

    raw_headers = [_map_header(c, j) for j, c in enumerate(raw.iloc[hdr_i].tolist())]
    body = raw.iloc[hdr_i + 1 :].copy().dropna(how="all")
    if body.empty:
        return pd.DataFrame()

    finalized = _finalize_headers_with_body(raw_headers, body)
    clean_headers = _dedupe_headers(finalized)
    body.columns = clean_headers
    if "description" not in body.columns:
        return pd.DataFrame()

    body["description"] = body["description"].astype(str).str.strip()
    body = body[
        body["description"].notna()
        & (body["description"] != "")
        & (body["description"].str.lower() != "nan")
    ]
    body = body[~body["description"].str.upper().isin({"DESCRIPTION", "ITEM", "TOTAL", "SUBTOTAL"})]

    vendor_label, sheet_label = _parse_sheet_name(sheet)
    shared_sheet = _sheet_looks_shared(sheet)
    rows = []
    for _, r in body.iterrows():
        # Back-team tagged this line for Kisan / other store — skip
        if _row_is_partner_store(r):
            continue
        desc = str(r.get("description") or "").strip()
        if not desc:
            continue
        vendor = str(r.get("vendor_name") or "").strip()
        if not vendor or vendor.lower() == "nan":
            vendor = vendor_label.split()[0] if vendor_label else ""
        upc = _norm_upc(r.get("upc")) or _norm_upc(r.get("vendor_name"))
        if str(upc).upper() == "KISAN":
            continue
        pos_name = str(r.get("pos_name") or "").strip()
        if pos_name.lower() == "nan":
            pos_name = ""
        qty, qty_total, qty_source, qty_kind = _resolve_ours_cases(r, sheet)
        case_count = _to_float(r.get("case_count"))
        if qty is None or qty <= 0:
            continue
        if qty_kind == "pieces":
            units = float(qty)
            cases_for_pattern = float(qty)
            case_count_out = 1.0
        else:
            cases_for_pattern = float(qty)
            case_count_out = float(case_count) if case_count and case_count > 0 else None
            units = cases_for_pattern * case_count_out if case_count_out else cases_for_pattern
        rows.append(
            {
                "invoice_sheet": sheet_label,
                "vendor_name": vendor,
                "description": desc,
                "pos_name": pos_name,
                "upc": upc,
                "item_code": str(r.get("item_code") or "").strip(),
                "cases_ordered": cases_for_pattern,
                "cases_total_shared": float(qty_total) if qty_total else None,
                "qty_source": qty_source,
                "shared_order": bool(shared_sheet or (qty_total is not None and qty_total > (qty or 0))),
                "case_count": case_count_out,
                "units_ordered": float(units),
                "qty_on_hand": _to_float(r.get("qty_on_hand")),
                "norm_desc": _norm_name(desc),
                "norm_pos": _norm_name(pos_name) if pos_name else "",
            }
        )
    return pd.DataFrame(rows)


def _parse_excel_folder(folder: Path) -> pd.DataFrame:
    """Parse all invoice workbooks once (ExcelFile kept open per file)."""
    frames: list[pd.DataFrame] = []
    for xlsx in _invoice_xlsx_files(folder):
        try:
            xl = pd.ExcelFile(xlsx)
        except Exception:
            continue
        # Read all sheets in one pass (avoids reopening the workbook per sheet)
        try:
            all_sheets = pd.read_excel(xl, sheet_name=None, header=None, dtype=object)
        except Exception:
            all_sheets = {}
            for sheet in xl.sheet_names:
                try:
                    all_sheets[sheet] = pd.read_excel(xl, sheet_name=sheet, header=None, dtype=object)
                except Exception:
                    continue
        for sheet, raw in all_sheets.items():
            try:
                df = _load_one_sheet_from_frame(raw, sheet)
            except Exception:
                continue
            if not df.empty:
                df["source_file"] = xlsx.name
                frames.append(df)
    if not frames:
        return pd.DataFrame()
    return _repair_case_units(pd.concat(frames, ignore_index=True))


def _patterns_from_lines(lines: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if lines.empty:
        return {}

    patterns: dict[str, dict[str, Any]] = {}

    def _agg(group: pd.DataFrame) -> dict[str, Any]:
        cases = group["cases_ordered"].dropna()
        units = group["units_ordered"].dropna()
        packs = group["case_count"].dropna()
        # If units missing but we have cases + pack, derive units for stats
        if units.empty and not cases.empty and not packs.empty:
            pack = float(packs.mode().iloc[0])
            units = cases * pack
        return {
            "order_count": int(len(group)),
            "median_cases": float(cases.median()) if not cases.empty else 0.0,
            "max_cases": float(cases.max()) if not cases.empty else 0.0,
            "p90_cases": float(cases.quantile(0.9)) if len(cases) >= 3 else float(cases.max() if not cases.empty else 0.0),
            "median_units": float(units.median()) if not units.empty else 0.0,
            "max_units": float(units.max()) if not units.empty else 0.0,
            "p90_units": float(units.quantile(0.9)) if len(units) >= 3 else float(units.max() if not units.empty else 0.0),
            "typical_case_count": float(packs.mode().iloc[0]) if not packs.empty else None,
            "sample_name": str(group["description"].iloc[0]),
        }

    with_upc = lines[lines["upc"].astype(str).str.len() >= 8]
    if not with_upc.empty:
        for upc, g in with_upc.groupby("upc"):
            patterns[f"upc:{upc}"] = _agg(g)

    for col, prefix in (("norm_pos", "pos"), ("norm_desc", "desc")):
        sub = lines[lines[col].astype(str).str.len() >= 4]
        if sub.empty:
            continue
        for key, g in sub.groupby(col):
            patterns[f"{prefix}:{key}"] = _agg(g)

    return patterns


def _write_patterns_csv(patterns: dict[str, dict[str, Any]]) -> None:
    if not patterns:
        return
    rows = []
    for key, pat in patterns.items():
        kind, _, rest = key.partition(":")
        rows.append(
            {
                "match_key": key,
                "match_type": kind,
                "match_value": rest,
                "sample_name": pat.get("sample_name"),
                "order_count": pat.get("order_count"),
                "median_units": pat.get("median_units"),
                "p90_units": pat.get("p90_units"),
                "max_units": pat.get("max_units"),
                "median_cases": pat.get("median_cases"),
                "p90_cases": pat.get("p90_cases"),
                "max_cases": pat.get("max_cases"),
                "typical_case_count": pat.get("typical_case_count"),
            }
        )
    out = pd.DataFrame(rows).sort_values(["match_type", "order_count"], ascending=[True, False])
    PATTERNS_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(PATTERNS_CSV, index=False)


def _save_caches(folder: Path, lines: pd.DataFrame, patterns: dict[str, dict[str, Any]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not lines.empty:
        lines.to_parquet(LINES_CACHE, index=False)
    PATTERNS_CACHE.write_text(json.dumps(patterns), encoding="utf-8")
    SIG_CACHE.write_text(_source_signature(folder), encoding="utf-8")
    _write_patterns_csv(patterns)


@lru_cache(maxsize=1)
def load_past_invoice_lines(path: str | None = None) -> pd.DataFrame:
    """Load all invoice sheet lines from Past Invoices folder (disk-cached)."""
    folder = Path(path) if path else PAST_INVOICES_DIR
    if not folder.exists():
        return pd.DataFrame()

    if _cache_fresh(folder) and LINES_CACHE.exists():
        try:
            return pd.read_parquet(LINES_CACHE)
        except Exception:
            pass

    lines = _parse_excel_folder(folder)
    patterns = _patterns_from_lines(lines)
    try:
        _save_caches(folder, lines, patterns)
    except Exception:
        pass
    return lines


@lru_cache(maxsize=1)
def build_invoice_order_patterns(path: str | None = None) -> dict[str, dict[str, Any]]:
    """
    Per-product historical order pattern from invoices.

    Keys: UPC (preferred) and normalized POS/description names.
    Values include median/max cases & units — used as shop-size / shelf caps.
    """
    folder = Path(path) if path else PAST_INVOICES_DIR
    if _cache_fresh(folder) and PATTERNS_CACHE.exists():
        try:
            return json.loads(PATTERNS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass

    lines = load_past_invoice_lines(path)
    patterns = _patterns_from_lines(lines)
    try:
        _save_caches(folder, lines, patterns)
    except Exception:
        pass
    return patterns


def lookup_invoice_pattern(
    patterns: dict[str, dict[str, Any]],
    *,
    upc: str = "",
    description: str = "",
    pos_name: str = "",
) -> dict[str, Any] | None:
    """Find best matching invoice pattern for a product."""
    if not patterns:
        return None
    u = _norm_upc(upc)
    if u and f"upc:{u}" in patterns:
        return patterns[f"upc:{u}"]
    # try zero-padded variants
    if u and u.isdigit():
        for w in (12, 13, 14):
            key = f"upc:{u.zfill(w)}"
            if key in patterns:
                return patterns[key]
        # Invoice sheets often store short UPC tails (e.g. 9627 vs 0071210789627)
        best_upc: dict[str, Any] | None = None
        best_n = -1
        for key, pat in patterns.items():
            if not key.startswith("upc:"):
                continue
            iu = key[4:]
            if not iu.isdigit():
                continue
            if len(iu) < 4 or len(u) < 4:
                continue
            if u.endswith(iu) or iu.endswith(u):
                n = int(pat.get("order_count") or 0)
                if n > best_n:
                    best_upc, best_n = pat, n
        if best_upc is not None:
            return best_upc

    candidates: list[dict[str, Any]] = []
    for name, prefix in ((pos_name, "pos"), (description, "desc"), (description, "pos")):
        n = _norm_name(name)
        if n and f"{prefix}:{n}" in patterns:
            candidates.append(patterns[f"{prefix}:{n}"])
    # soft contains / token overlap — prefer the pattern with the most invoice history
    n = _norm_name(description)
    if n and len(n) >= 8:
        tokens = set(n.split())
        for key, pat in patterns.items():
            if not key.startswith("desc:"):
                continue
            kn = key[5:]
            if n in kn or kn in n:
                candidates.append(pat)
                continue
            kt = set(kn.split())
            inter = tokens & kt
            # e.g. POS "AMUL MILK GOLD 1GAL" ↔ invoice "AMUL MILK GOLD 6% FAT 4/1 GAL"
            if len(inter) >= 3:
                candidates.append(pat)
    if not candidates:
        return None
    return max(candidates, key=lambda p: (int(p.get("order_count") or 0), float(p.get("max_cases") or 0)))


def apply_invoice_order_cap(
    order_qty: int,
    pattern: dict[str, Any] | None,
    *,
    buffer: float = 1.25,
    min_qty: int = 0,
    pack_size: int = 1,
) -> tuple[int, str]:
    """
    Cap AI units using historical invoice sizes (shop / shelf reality).

    Invoice QTY GIVEN is cases. Cap is computed in units:
      max(p90_units, p90_cases × pack) × buffer
    ``min_qty`` floors the cap (e.g. expected sales need).
    """
    if not pattern or order_qty <= 0:
        return int(order_qty), ""

    p90 = float(pattern.get("p90_units") or 0)
    mx = float(pattern.get("max_units") or 0)
    med = float(pattern.get("median_units") or 0)
    pack = max(int(pack_size or 1), 1)
    # Also honor case history × current pack (QTY GIVEN was cases, not units)
    p90_c = float(pattern.get("p90_cases") or 0)
    mx_c = float(pattern.get("max_cases") or 0)
    med_c = float(pattern.get("median_cases") or 0)
    if pack > 1:
        p90 = max(p90, p90_c * pack, mx_c * pack, med_c * pack)
        mx = max(mx, mx_c * pack)
        med = max(med, med_c * pack)

    cap_units = max(p90, med, mx, 0.0)
    if cap_units <= 0:
        return int(order_qty), ""

    cap = int(max(round(cap_units * buffer), round(med), 1))
    floor = max(int(min_qty or 0), 0)
    if floor > 0:
        cap = max(cap, floor)
    # Never raise the order via invoice history — only cap oversized suggestions
    if order_qty <= cap:
        return int(order_qty), ""

    floor_txt = f"; floor expected {floor}" if floor else ""
    note = (
        f"Capped {order_qty} -> {cap} using past invoices "
        f"(hist max {mx_c:g} cases / {mx:g} units{floor_txt}; "
        f"{int(pattern.get('order_count') or 0)} past orders)"
    )
    return cap, note


def clear_invoice_caches(*, wipe_disk: bool = False) -> None:
    """Clear in-memory caches. Set wipe_disk=True after replacing invoice Excel files."""
    load_past_invoice_lines.cache_clear()
    build_invoice_order_patterns.cache_clear()
    if wipe_disk:
        for p in (LINES_CACHE, PATTERNS_CACHE, SIG_CACHE):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
