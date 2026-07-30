"""
Import local recovered store data → Paul tenant tables (match existing schema).

Sources → tables
  data/inventory/current inventory count.csv
      → vendors (names)
      → product_vendor (UPC → product_id × vendor)
      → product_locations.quantity (on-hand)
  data/sales/Product Sales*.csv
      → ai_pos_daily_sales  (NOT live POS orders)

Does NOT wipe live ``orders`` / ``order_items``.
Gift-card CSV is skipped when codes are Excel-mangled (scientific notation).

Requires SSH tunnel: 127.0.0.1:5433

  python scripts/import_local_to_paul.py
  python scripts/import_local_to_paul.py --execute
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from config.data_paths import INVENTORY_PATH
from config.settings import PROJECT_ROOT, get_settings
from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident
from v2.forecasting.local_pos_sales import load_local_pos_daily_sales, normalize_upc
from v2.forecasting.sales_loader import load_upc_to_product_id

AI_SALES_TABLE = "ai_pos_daily_sales"
DEFAULT_WAREHOUSE_ID = 1
DEFAULT_PICKING_LOCATION_ID = 4
DEFAULT_ADDED_BY = 1


def _load_inventory() -> pd.DataFrame:
    if not INVENTORY_PATH.exists():
        raise SystemExit(f"missing inventory file: {INVENTORY_PATH}")
    raw = pd.read_csv(INVENTORY_PATH, dtype=str)
    cols = {c.strip().lower(): c for c in raw.columns}
    upc_col = cols.get("upc")
    qty_col = (
        cols.get("quantityonhand")
        or cols.get("quantity_on_hand")
        or cols.get("quantity")
    )
    vendor_col = cols.get("vendor_name")
    cost_col = cols.get("cost")
    pack_col = cols.get("pack")
    if not upc_col or not qty_col:
        raise SystemExit("inventory CSV needs upc + QuantityOnHand")

    out = pd.DataFrame(
        {
            "upc": raw[upc_col].map(normalize_upc),
            "quantity": pd.to_numeric(raw[qty_col], errors="coerce").fillna(0.0),
            "vendor_name": (
                raw[vendor_col].fillna("").astype(str).str.strip()
                if vendor_col
                else ""
            ),
            "cost": (
                pd.to_numeric(raw[cost_col], errors="coerce")
                if cost_col
                else pd.NA
            ),
            "pack": (
                pd.to_numeric(raw[pack_col], errors="coerce")
                if pack_col
                else pd.NA
            ),
        }
    )
    out = out[out["upc"] != ""]
    # drop blank / placeholder vendors for vendor table, keep for stock
    return out.reset_index(drop=True)


def _norm_vendor_name(name: str) -> str:
    s = re.sub(r"\s+", " ", (name or "").strip())
    return s


def _ensure_ai_sales_table(db: WecommDatabaseConnector, sch: str) -> None:
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {sch}.{AI_SALES_TABLE} (
          id BIGSERIAL PRIMARY KEY,
          sale_date DATE NOT NULL,
          upc TEXT NOT NULL,
          product_id BIGINT NULL,
          quantity NUMERIC NOT NULL DEFAULT 0,
          net_sales NUMERIC NULL,
          description TEXT NULL,
          source_file TEXT NULL,
          imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_ai_pos_daily_sales_date
          ON {sch}.{AI_SALES_TABLE} (sale_date)
        """
    )
    db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_ai_pos_daily_sales_product
          ON {sch}.{AI_SALES_TABLE} (product_id)
        """
    )


def _existing_vendors(db: WecommDatabaseConnector, sch: str) -> dict[str, int]:
    df = db.read_sql(
        f"""
        SELECT id, name
        FROM {sch}.vendors
        WHERE deleted_at IS NULL
        """
    )
    out: dict[str, int] = {}
    for r in df.itertuples(index=False):
        out[_norm_vendor_name(str(r.name)).lower()] = int(r.id)
    return out


def import_vendors(
    db: WecommDatabaseConnector,
    sch: str,
    inv: pd.DataFrame,
    *,
    execute: bool,
) -> dict[str, int]:
    names = sorted(
        {
            _norm_vendor_name(n)
            for n in inv["vendor_name"].tolist()
            if _norm_vendor_name(n)
            and _norm_vendor_name(n).upper() not in {"UPDATE VENDOR", "BLANK", "NONE", "NAN"}
        }
    )
    existing = _existing_vendors(db, sch)
    print(f"local distinct vendors={len(names)}  paul_vendors_now={len(existing)}")

    created = 0
    for name in names:
        key = name.lower()
        if key in existing:
            continue
        print(f"  + vendor '{name}'")
        if execute:
            with db.engine.begin() as conn:
                row = conn.execute(
                    text(
                        f"""
                        INSERT INTO {sch}.vendors (name, vendor_type, created_at, updated_at)
                        VALUES (:name, 'supplier', NOW(), NOW())
                        RETURNING id
                        """
                    ),
                    {"name": name},
                ).first()
                existing[key] = int(row[0])
        else:
            existing[key] = -1  # placeholder for dry-run mapping counts
        created += 1

    print(f"vendors to_create={created} (execute={execute})")
    if execute:
        existing = _existing_vendors(db, sch)
    return existing


def _product_vendor_frame(
    inv: pd.DataFrame,
    upc_map: dict[str, str],
    vendor_map: dict[str, int],
) -> pd.DataFrame:
    tmp = inv.copy()
    tmp["product_id"] = tmp["upc"].map(
        lambda u: int(upc_map[u]) if u in upc_map else pd.NA
    )
    tmp["vendor_key"] = tmp["vendor_name"].map(
        lambda n: _norm_vendor_name(str(n)).lower()
    )
    tmp["vendor_id"] = tmp["vendor_key"].map(
        lambda k: vendor_map.get(k) if vendor_map.get(k, 0) and vendor_map.get(k, 0) > 0 else pd.NA
    )
    # In dry-run, newly staged vendors are -1 — still count by vendor_key presence
    if any(v == -1 for v in vendor_map.values()):
        known = set(vendor_map.keys())
        tmp.loc[
            tmp["vendor_id"].isna() & tmp["vendor_key"].isin(known),
            "vendor_id",
        ] = -1

    skip = {"update vendor", "blank", "none", "nan", ""}
    tmp = tmp[tmp["product_id"].notna() & tmp["vendor_key"].notna()]
    tmp = tmp[~tmp["vendor_key"].isin(skip)]
    tmp = tmp[tmp["vendor_id"].notna()]
    tmp["price"] = pd.to_numeric(tmp["cost"], errors="coerce")
    return (
        tmp.sort_values("quantity", ascending=False)
        .drop_duplicates(subset=["product_id", "vendor_key"])
        [["product_id", "vendor_id", "vendor_key", "price"]]
    )


def import_product_vendor(
    db: WecommDatabaseConnector,
    sch: str,
    inv: pd.DataFrame,
    upc_map: dict[str, str],
    vendor_map: dict[str, int],
    *,
    execute: bool,
) -> None:
    uniq = _product_vendor_frame(inv, upc_map, vendor_map)
    unmatched_upc = int((~inv["upc"].isin(upc_map.keys())).sum())
    print(
        f"product_vendor links={len(uniq)} unmatched_upc_rows={unmatched_upc}"
    )
    if not execute:
        print("[dry-run] would DELETE+INSERT product_vendor")
        return

    # refresh vendor ids after inserts
    vendor_map = _existing_vendors(db, sch)
    uniq = _product_vendor_frame(inv, upc_map, vendor_map)
    uniq = uniq[uniq["vendor_id"] > 0]
    print(f"product_vendor inserting={len(uniq)} (clear old links first)")

    with db.engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {sch}.product_vendor"))
        for r in uniq.itertuples(index=False):
            conn.execute(
                text(
                    f"""
                    INSERT INTO {sch}.product_vendor
                      (product_id, vendor_id, price, lead_time_days, created_at, updated_at)
                    VALUES
                      (:pid, :vid, :price, NULL, NOW(), NOW())
                    """
                ),
                {
                    "pid": int(r.product_id),
                    "vid": int(r.vendor_id),
                    "price": float(r.price) if pd.notna(r.price) else None,
                },
            )


def import_inventory_locations(
    db: WecommDatabaseConnector,
    sch: str,
    inv: pd.DataFrame,
    upc_map: dict[str, str],
    *,
    execute: bool,
) -> None:
    # aggregate qty by upc
    stock = inv.groupby("upc", as_index=False)["quantity"].sum()
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
    pid_to_loc = (
        {
            int(r.product_id): int(r.id)
            for r in locs.drop_duplicates("product_id").itertuples(index=False)
        }
        if not locs.empty
        else {}
    )

    updates = 0
    inserts = 0
    unmapped = 0
    pending_inserts: list[tuple[int, float]] = []
    pending_updates: list[tuple[int, float]] = []

    for r in stock.itertuples(index=False):
        pid_s = upc_map.get(r.upc)
        if not pid_s:
            unmapped += 1
            continue
        pid = int(pid_s)
        qty = float(r.quantity)
        lid = pid_to_loc.get(pid)
        if lid is None:
            pending_inserts.append((pid, qty))
            inserts += 1
        else:
            pending_updates.append((lid, qty))
            updates += 1

    print(
        f"inventory update_locs={updates} insert_locs={inserts} unmapped_upc={unmapped}"
    )
    if not execute:
        print("[dry-run] no product_locations written")
        return

    with db.engine.begin() as conn:
        for lid, qty in pending_updates:
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
        for pid, qty in pending_inserts:
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
    print(f"wrote inventory updates={updates} inserts={inserts}")


def import_sales(
    db: WecommDatabaseConnector,
    sch: str,
    schema: str,
    upc_map: dict[str, str],
    *,
    execute: bool,
) -> None:
    sales = load_local_pos_daily_sales()
    print(
        f"local_sales rows={len(sales)} "
        f"days={sales['sale_date'].nunique() if not sales.empty else 0} "
        f"upcs={sales['upc'].nunique() if not sales.empty else 0}"
    )
    if sales.empty:
        return
    sales = sales.copy()
    sales["product_id"] = sales["upc"].map(
        lambda u: int(upc_map[u]) if u in upc_map else pd.NA
    )
    print(
        f"sales upc->product matched={int(sales['product_id'].notna().sum())}/{len(sales)}"
    )
    if not execute:
        print(f"[dry-run] would TRUNCATE+INSERT {len(sales)} → {schema}.{AI_SALES_TABLE}")
        return

    _ensure_ai_sales_table(db, sch)
    db.execute(f"TRUNCATE TABLE {sch}.{AI_SALES_TABLE}")
    upload = sales[
        [
            "sale_date",
            "upc",
            "product_id",
            "quantity",
            "net_sales",
            "description",
            "source_file",
        ]
    ].copy()
    upload["sale_date"] = pd.to_datetime(upload["sale_date"]).dt.date
    upload["product_id"] = upload["product_id"].astype(object).where(
        upload["product_id"].notna(), None
    )
    n = db.write_dataframe(upload, AI_SALES_TABLE, schema=schema, if_exists="append")
    print(f"wrote {n} rows → {schema}.{AI_SALES_TABLE}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--skip-sales", action="store_true")
    parser.add_argument("--skip-inventory", action="store_true")
    parser.add_argument("--skip-vendors", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    get_settings.cache_clear()

    schema = get_tenant_schema()
    sch = q_ident(schema)
    print("TENANT_SCHEMA", schema)
    print("MODE", "EXECUTE" if args.execute else "DRY-RUN")

    db = WecommDatabaseConnector()
    try:
        print("db_ok", int(db.read_sql("SELECT 1 AS ok").iloc[0]["ok"]))
    except Exception as exc:
        print("ERROR: tunnel down — start Bastion + SSH :5433")
        print(exc)
        return 2

    before = {
        t: int(db.read_sql(f"SELECT COUNT(*) AS n FROM {sch}.{t}").iloc[0]["n"])
        for t in ("vendors", "product_vendor", "product_locations")
    }
    print("BEFORE", before)

    inv = _load_inventory()
    print(f"inventory_rows={len(inv)}")
    upc_map = load_upc_to_product_id(schema=schema, connector=db)
    print(f"barcode_map_size={len(upc_map)}")

    vendor_map: dict[str, int] = _existing_vendors(db, sch)
    if not args.skip_vendors:
        vendor_map = import_vendors(db, sch, inv, execute=args.execute)
        import_product_vendor(
            db, sch, inv, upc_map, vendor_map, execute=args.execute
        )

    if not args.skip_inventory:
        import_inventory_locations(
            db, sch, inv, upc_map, execute=args.execute
        )

    if not args.skip_sales:
        import_sales(db, sch, schema, upc_map, execute=args.execute)

    if args.execute:
        after = {
            t: int(db.read_sql(f"SELECT COUNT(*) AS n FROM {sch}.{t}").iloc[0]["n"])
            for t in ("vendors", "product_vendor", "product_locations")
        }
        try:
            after[AI_SALES_TABLE] = int(
                db.read_sql(f"SELECT COUNT(*) AS n FROM {sch}.{AI_SALES_TABLE}").iloc[0][
                    "n"
                ]
            )
        except Exception:
            after[AI_SALES_TABLE] = 0
        print("AFTER", after)
        print("sample vendors:")
        print(
            db.read_sql(
                f"SELECT id, name FROM {sch}.vendors WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 10"
            ).to_string(index=False)
        )

    print("DONE — live POS orders untouched; gift_cards skipped (codes mangled in CSV).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
