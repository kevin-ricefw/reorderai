"""Load daily demand series — prefer rich local POS CSVs, else Paul orders."""

from __future__ import annotations

import os
import re

import pandas as pd

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident
from v2.forecasting.local_pos_sales import (
    load_local_pos_daily_sales,
    local_sales_to_demand,
    normalize_upc,
)


def _use_local_sales() -> bool:
    # Default ON when local dated Product Sales files exist — that dump is the
    # real demand history for uplift (weekday / weekend / festival).
    flag = os.getenv("FORECAST_USE_LOCAL_SALES", "auto").lower()
    if flag in {"0", "false", "no", "paul", "db"}:
        return False
    if flag in {"1", "true", "yes", "local"}:
        return True
    # auto
    sample = load_local_pos_daily_sales(lookback_days=None)
    return not sample.empty


def load_upc_to_product_id(
    *,
    schema: str | None = None,
    connector: WecommDatabaseConnector | None = None,
) -> dict[str, str]:
    """normalized UPC → product_id (str). Best-effort; empty if tunnel down."""
    try:
        db = connector or WecommDatabaseConnector()
        sch = q_ident(schema or get_tenant_schema())
        df = db.read_sql(
            f"""
            SELECT barcode, product_id
            FROM {sch}.product_barcodes
            WHERE barcode IS NOT NULL
            """
        )
    except Exception:
        return {}
    if df.empty:
        return {}
    out: dict[str, str] = {}
    for r in df.itertuples(index=False):
        upc = normalize_upc(r.barcode)
        if upc:
            out[upc] = str(int(r.product_id))
            digits = re.sub(r"\D", "", str(r.barcode))
            if digits:
                out[digits] = str(int(r.product_id))
    return out


def _lookback_clause(column_sql: str, lookback_days: int | None) -> str:
    """Empty string = all history. lookback_days <= 0 means all."""
    if lookback_days is None or int(lookback_days) <= 0:
        return ""
    days = max(int(lookback_days), 14)
    return f"AND {column_sql} >= (CURRENT_DATE - INTERVAL '{days} days')"


def load_daily_demand_from_paul(
    *,
    lookback_days: int | None = 0,
    schema: str | None = None,
    connector: WecommDatabaseConnector | None = None,
) -> pd.DataFrame:
    db = connector or WecommDatabaseConnector()
    sch = q_ident(schema or get_tenant_schema())
    lb = _lookback_clause("DATE(o.created_at)", lookback_days)

    df = db.read_sql(
        f"""
        SELECT
          oi.product_id AS item_id,
          DATE(o.created_at) AS sale_date,
          SUM(
            GREATEST(
              COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0),
              0
            )
          ) AS quantity
        FROM {sch}.order_items oi
        JOIN {sch}.orders o ON o.id = oi.order_id
        WHERE o.deleted_at IS NULL
          AND oi.deleted_at IS NULL
          AND COALESCE(o.is_return, FALSE) = FALSE
          {lb}
        GROUP BY oi.product_id, DATE(o.created_at)
        ORDER BY oi.product_id, DATE(o.created_at)
        """
    )
    if df.empty:
        return pd.DataFrame(columns=["item_id", "date", "quantity"])

    return pd.DataFrame(
        {
            "item_id": df["item_id"].astype(str),
            "date": pd.to_datetime(df["sale_date"]),
            "quantity": pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0),
        }
    )


def load_daily_demand_from_ai_table(
    *,
    lookback_days: int | None = 0,
    schema: str | None = None,
    connector: WecommDatabaseConnector | None = None,
) -> pd.DataFrame:
    """Read ``ai_pos_daily_sales`` if the import script has populated it."""
    try:
        db = connector or WecommDatabaseConnector()
        sch = q_ident(schema or get_tenant_schema())
        lb = _lookback_clause("sale_date", lookback_days)
        where_extra = f"WHERE 1=1 {lb}" if lb else ""
        df = db.read_sql(
            f"""
            SELECT
              COALESCE(product_id::text, upc) AS item_id,
              sale_date,
              SUM(quantity) AS quantity
            FROM {sch}.ai_pos_daily_sales
            {where_extra}
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        )
    except Exception:
        return pd.DataFrame(columns=["item_id", "date", "quantity"])
    if df.empty:
        return pd.DataFrame(columns=["item_id", "date", "quantity"])
    return pd.DataFrame(
        {
            "item_id": df["item_id"].astype(str),
            "date": pd.to_datetime(df["sale_date"]),
            "quantity": pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0),
        }
    )


def load_daily_demand(
    *,
    lookback_days: int | None = 0,
    schema: str | None = None,
    connector: WecommDatabaseConnector | None = None,
) -> pd.DataFrame:
    """
    Returns columns: item_id (str), date (datetime64[ns]), quantity (float)

    lookback_days: 0 or None = use full available history (recommended).

    Priority:
      1. Local ``data/sales/Product Sales*.csv`` when FORECAST_USE_LOCAL_SALES=auto/1
      2. Paul ``ai_pos_daily_sales`` (after import)
      3. Paul ``orders`` × ``order_items``
    """
    # Normalize: <=0 means all history for local loader too
    lb = None if lookback_days is None or int(lookback_days) <= 0 else int(lookback_days)

    if _use_local_sales():
        local = load_local_pos_daily_sales(lookback_days=lb)
        if not local.empty:
            upc_map = load_upc_to_product_id(schema=schema, connector=connector)
            return local_sales_to_demand(local, upc_to_item_id=upc_map or None)

    ai = load_daily_demand_from_ai_table(
        lookback_days=lb, schema=schema, connector=connector
    )
    if not ai.empty:
        return ai

    return load_daily_demand_from_paul(
        lookback_days=lb, schema=schema, connector=connector
    )


def load_item_categories(
    *,
    schema: str | None = None,
    connector: WecommDatabaseConnector | None = None,
) -> dict[str, str]:
    """Map product_id → category name (lower) for Phase-2 uplift."""
    db = connector or WecommDatabaseConnector()
    sch = q_ident(schema or get_tenant_schema())
    try:
        df = db.read_sql(
            f"""
            SELECT p.id AS item_id, COALESCE(c.name, '') AS category_name
            FROM {sch}.products p
            LEFT JOIN {sch}.categories c ON c.id = p.category_id
            WHERE p.deleted_at IS NULL
            """
        )
    except Exception:
        return {}
    if df.empty:
        return {}
    return {
        str(int(r.item_id)): str(r.category_name or "").lower()
        for r in df.itertuples(index=False)
    }
