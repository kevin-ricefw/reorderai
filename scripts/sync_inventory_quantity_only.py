"""
Quantity-only sync: data/inventory/products.csv → Teja product_locations.quantity

- Matches UPC with leading-zero-safe normalize (does NOT rewrite barcodes)
- Updates quantity only on existing location rows
- Inserts a minimal picking location row only when product has none (needed to store qty)
- Does not change products, vendors, barcodes, prices, packs, sales, etc.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident
from v2.forecasting.local_pos_sales import normalize_upc

CSV_PATH = ROOT / "data" / "inventory" / "products.csv"
DEFAULT_WAREHOUSE_ID = 1
DEFAULT_PICKING_LOCATION_ID = 4
DEFAULT_ADDED_BY = 1


def _load_counts() -> pd.DataFrame:
    raw = pd.read_csv(CSV_PATH, dtype=str)
    cols = {c.strip().lower(): c for c in raw.columns}
    upc_col = cols.get("upc")
    qty_col = (
        cols.get("quantityonhand")
        or cols.get("quantity_on_hand")
        or cols.get("quantity")
    )
    if not upc_col or not qty_col:
        raise SystemExit("products.csv needs upc + QuantityOnHand")
    out = pd.DataFrame(
        {
            "upc_raw": raw[upc_col].fillna("").astype(str).str.strip(),
            "upc_norm": raw[upc_col].map(normalize_upc),
            "quantity": pd.to_numeric(raw[qty_col], errors="coerce"),
        }
    )
    out = out[out["upc_norm"] != ""]
    out = out[out["quantity"].notna()]
    # one qty per normalized UPC (last wins if duplicates)
    out = out.drop_duplicates(subset=["upc_norm"], keep="last")
    return out.reset_index(drop=True)


def _barcode_maps(db: WecommDatabaseConnector, sch: str) -> dict[str, int]:
    """Map normalized UPC (+ raw digits) → product_id. Never writes barcodes."""
    df = db.read_sql(
        f"""
        SELECT barcode, product_id
        FROM {sch}.product_barcodes
        WHERE barcode IS NOT NULL
        """
    )
    m: dict[str, int] = {}
    for r in df.itertuples(index=False):
        pid = int(r.product_id)
        raw = str(r.barcode).strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        for key in {normalize_upc(raw), digits, digits.lstrip("0") or "0"}:
            if key:
                m[key] = pid
    return m


def main() -> int:
    schema = get_tenant_schema()
    sch = q_ident(schema)
    print("TENANT_SCHEMA", schema)
    print("CSV", CSV_PATH)
    if not CSV_PATH.exists():
        raise SystemExit(f"missing {CSV_PATH}")

    db = WecommDatabaseConnector()
    print("db_ok", int(db.read_sql("SELECT 1 AS ok").iloc[0]["ok"]))

    counts = _load_counts()
    print(f"csv_qty_rows={len(counts)}")

    upc_to_pid = _barcode_maps(db, sch)
    print(f"barcode_keys={len(upc_to_pid)}")

    locs = db.read_sql(
        f"""
        SELECT id, product_id
        FROM {sch}.product_locations
        WHERE deleted_at IS NULL
          AND location_type = 'picking'
        ORDER BY id
        """
    )
    if locs.empty:
        locs = db.read_sql(
            f"""
            SELECT id, product_id
            FROM {sch}.product_locations
            WHERE deleted_at IS NULL
            ORDER BY id
            """
        )
    pid_to_loc: dict[int, int] = {}
    if not locs.empty:
        for r in locs.drop_duplicates("product_id").itertuples(index=False):
            pid_to_loc[int(r.product_id)] = int(r.id)

    before = int(
        db.read_sql(f"SELECT COUNT(*) AS n FROM {sch}.product_locations").iloc[0]["n"]
    )
    print("product_locations_before", before)

    matched = 0
    unmatched = 0
    updates: list[tuple[int, float]] = []
    inserts: list[tuple[int, float]] = []
    unmatched_upcs: list[str] = []

    for r in counts.itertuples(index=False):
        pid = upc_to_pid.get(r.upc_norm)
        if pid is None and r.upc_raw:
            digits = "".join(ch for ch in str(r.upc_raw) if ch.isdigit())
            pid = upc_to_pid.get(digits) or upc_to_pid.get(digits.lstrip("0") or "0")
        if pid is None:
            unmatched += 1
            if len(unmatched_upcs) < 20:
                unmatched_upcs.append(str(r.upc_raw))
            continue
        matched += 1
        qty = float(r.quantity)
        lid = pid_to_loc.get(int(pid))
        if lid is None:
            inserts.append((int(pid), qty))
        else:
            updates.append((lid, qty))

    print(f"matched_upc={matched} unmatched_upc={unmatched}")
    print(f"will_update={len(updates)} will_insert_location={len(inserts)}")
    if unmatched_upcs:
        print("unmatched_sample", unmatched_upcs)

    with db.engine.begin() as conn:
        for lid, qty in updates:
            conn.execute(
                text(
                    f"""
                    UPDATE {sch}.product_locations
                    SET quantity = :qty, updated_at = NOW()
                    WHERE id = :lid
                    """
                ),
                {"qty": qty, "lid": lid},
            )
        for pid, qty in inserts:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {sch}.product_locations (
                      quantity, product_id, created_at, updated_at,
                      min_quantity, max_quantity, warehouse_id,
                      replenishment_priority, warehouse_location_id,
                      added_by, location_type, location_name
                    ) VALUES (
                      :qty, :pid, NOW(), NOW(),
                      0, 0, :wh,
                      0, :wloc,
                      :added, 'picking', 'PICKING'
                    )
                    """
                ),
                {
                    "qty": qty,
                    "pid": pid,
                    "wh": DEFAULT_WAREHOUSE_ID,
                    "wloc": DEFAULT_PICKING_LOCATION_ID,
                    "added": DEFAULT_ADDED_BY,
                },
            )

    after = db.read_sql(
        f"""
        SELECT COUNT(*) AS loc_rows,
               COALESCE(SUM(quantity),0) AS sum_qty,
               COALESCE(SUM(CASE WHEN quantity > 0 THEN 1 ELSE 0 END),0) AS positive_rows,
               COALESCE(SUM(CASE WHEN quantity = 0 THEN 1 ELSE 0 END),0) AS zero_rows,
               COALESCE(SUM(CASE WHEN quantity < 0 THEN 1 ELSE 0 END),0) AS neg_rows
        FROM {sch}.product_locations
        WHERE deleted_at IS NULL
        """
    )
    print("AFTER")
    print(after.to_string(index=False))
    print("DONE quantity-only sync")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
