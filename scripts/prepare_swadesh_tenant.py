"""Prepare Swadesh Food Mart tenant before local data import."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident

SCHEMA = get_tenant_schema()
DISPLAY_NAME = "Swadesh Food Mart"


def main() -> int:
    db = WecommDatabaseConnector()
    sch = q_ident(SCHEMA)
    print("TENANT_SCHEMA", SCHEMA)

    # 1) Rename master tenant display name
    with db.engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE public.tenants
                SET data = jsonb_set(
                      COALESCE(data::jsonb, '{}'::jsonb),
                      '{name}',
                      to_jsonb(CAST(:name AS text)),
                      true
                    ),
                    updated_at = NOW()
                WHERE uuid::text = :uuid
                """
            ),
            {
                "name": DISPLAY_NAME,
                "uuid": SCHEMA.removeprefix("wecomm_"),
            },
        )
    print(f"tenant display name -> {DISPLAY_NAME}")

    # 2) Soft-delete demo firework vendors (not from store inventory)
    with db.engine.begin() as conn:
        res = conn.execute(
            text(
                f"""
                UPDATE {sch}.vendors
                SET deleted_at = NOW(), updated_at = NOW()
                WHERE deleted_at IS NULL
                  AND (
                    name ILIKE '%Walter Curtis%'
                    OR name ILIKE '%Fusee%'
                    OR name ILIKE '%Orion Safety%'
                  )
                """
            )
        )
        print(f"soft-deleted demo vendors: {res.rowcount}")

    # 3) Ensure warehouse exists for stock import
    wh = db.read_sql(f"SELECT id, name FROM {sch}.warehouses ORDER BY id")
    print("warehouses:\n", wh.to_string(index=False) if not wh.empty else "(none)")

    uuid = SCHEMA.removeprefix("wecomm_")
    row = db.read_sql(
        f"""
        SELECT id, data->>'name' AS name, data->>'tenancy_db_name' AS schema
        FROM public.tenants
        WHERE uuid::text = '{uuid}'
        """
    )
    print("tenant row:\n", row.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
