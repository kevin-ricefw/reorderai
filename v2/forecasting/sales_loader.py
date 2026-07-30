"""Load daily demand series from Wecomm tenant schema."""

from __future__ import annotations

import pandas as pd

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident


def load_daily_demand(
    *,
    lookback_days: int = 180,
    schema: str | None = None,
    connector: WecommDatabaseConnector | None = None,
) -> pd.DataFrame:
    """
    Returns columns: item_id (str), date (datetime64[ns]), quantity (float)
    Net of returns; excludes return orders.
    """
    db = connector or WecommDatabaseConnector()
    sch = q_ident(schema or get_tenant_schema())
    days = max(int(lookback_days), 14)

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
          AND o.created_at >= (NOW() - INTERVAL '{days} days')
        GROUP BY oi.product_id, DATE(o.created_at)
        ORDER BY oi.product_id, DATE(o.created_at)
        """
    )
    if df.empty:
        return pd.DataFrame(columns=["item_id", "date", "quantity"])

    out = pd.DataFrame(
        {
            "item_id": df["item_id"].astype(str),
            "date": pd.to_datetime(df["sale_date"]),
            "quantity": pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0),
        }
    )
    return out


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
