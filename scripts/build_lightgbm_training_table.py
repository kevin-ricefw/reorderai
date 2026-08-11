"""Build forecasting.daily_product_data and export CSV for Kevin."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

import os

from sqlalchemy import text

from database.connectors.wecomm import WecommDatabaseConnector

SCH = os.getenv("TENANT_SCHEMA") or "wecomm_019fed0f-416b-7094-b228-794dc42270b6"
OUT = Path.home() / "Desktop" / "forecasting_daily_product_data.csv"


DDL = [
    "CREATE SCHEMA IF NOT EXISTS forecasting",
    "DROP TABLE IF EXISTS forecasting.daily_product_data CASCADE",
    """
CREATE TABLE forecasting.daily_product_data (
    sale_date              date           NOT NULL,
    product_id             bigint         NOT NULL,
    upc                    varchar(255),
    sku                    varchar(255),
    product_name           varchar(255),
    category_id            bigint,
    category_name          varchar(255),
    list_price             numeric(15,4),
    purchase_price         numeric(15,4),
    is_scale               boolean        NOT NULL DEFAULT false,
    pack_size              integer,
    target_sales           numeric(15,4)  NOT NULL DEFAULT 0,
    gross_sales_qty        numeric(15,4)  NOT NULL DEFAULT 0,
    return_qty             numeric(15,4)  NOT NULL DEFAULT 0,
    order_lines            integer        NOT NULL DEFAULT 0,
    avg_unit_price         numeric(15,4),
    any_discount_flag      boolean        NOT NULL DEFAULT false,
    dow                    smallint,
    is_weekend             boolean        NOT NULL DEFAULT false,
    created_at             timestamp      NOT NULL DEFAULT now(),
    PRIMARY KEY (sale_date, product_id)
)
""",
    """
CREATE INDEX IF NOT EXISTS idx_forecast_dpd_product_date
    ON forecasting.daily_product_data (product_id, sale_date)
""",
    """
CREATE INDEX IF NOT EXISTS idx_forecast_dpd_date
    ON forecasting.daily_product_data (sale_date)
""",
]

INSERT_SQL = f"""
INSERT INTO forecasting.daily_product_data (
    sale_date, product_id, upc, sku, product_name, category_id, category_name,
    list_price, purchase_price, is_scale, pack_size,
    target_sales, gross_sales_qty, return_qty, order_lines,
    avg_unit_price, any_discount_flag, dow, is_weekend
)
WITH bounds AS (
    SELECT MIN(o.created_at)::date AS d0, MAX(o.created_at)::date AS d1
    FROM "{SCH}".orders o
    WHERE o.deleted_at IS NULL AND COALESCE(o.is_return, FALSE) = FALSE
),
dates AS (
    SELECT generate_series(b.d0, b.d1, INTERVAL '1 day')::date AS sale_date
    FROM bounds b
),
upc_one AS (
    SELECT DISTINCT ON (pb.product_id)
        pb.product_id, pb.barcode AS upc
    FROM "{SCH}".product_barcodes pb
    ORDER BY pb.product_id, pb.id
),
products AS (
    SELECT
        p.id AS product_id,
        p.name AS product_name,
        p.sku,
        p.category_id,
        c.name AS category_name,
        p.price AS list_price,
        p.purchase_price,
        COALESCE(p.scale, FALSE) AS is_scale,
        COALESCE(NULLIF(p.min_reorder_quantity, 0), 1) AS pack_size,
        u.upc
    FROM "{SCH}".products p
    LEFT JOIN "{SCH}".categories c
      ON c.id = p.category_id AND c.deleted_at IS NULL
    LEFT JOIN upc_one u ON u.product_id = p.id
    WHERE p.deleted_at IS NULL
),
daily_sales AS (
    SELECT
        o.created_at::date AS sale_date,
        oi.product_id,
        SUM(GREATEST(COALESCE(oi.quantity, 0), 0)) AS gross_sales_qty,
        SUM(GREATEST(COALESCE(oi.returned_quantity, 0), 0)) AS return_qty,
        SUM(GREATEST(COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0), 0)) AS target_sales,
        COUNT(*) AS order_lines,
        CASE
            WHEN SUM(GREATEST(COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0), 0)) > 0
            THEN SUM(COALESCE(oi.total_price, 0))
                 / NULLIF(SUM(GREATEST(COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0), 0)), 0)
            ELSE NULL
        END AS avg_unit_price,
        BOOL_OR(
            COALESCE(oi.discount_amount, 0) > 0
            OR COALESCE(oi.total_allocated_discount, 0) > 0
            OR COALESCE(oi.total_allocated_promotion_discount, 0) > 0
        ) AS any_discount_flag
    FROM "{SCH}".order_items oi
    JOIN "{SCH}".orders o ON o.id = oi.order_id
    WHERE o.deleted_at IS NULL
      AND oi.deleted_at IS NULL
      AND COALESCE(o.is_return, FALSE) = FALSE
    GROUP BY o.created_at::date, oi.product_id
)
SELECT
    d.sale_date,
    p.product_id,
    p.upc,
    p.sku,
    p.product_name,
    p.category_id,
    p.category_name,
    p.list_price,
    p.purchase_price,
    p.is_scale,
    p.pack_size,
    COALESCE(s.target_sales, 0),
    COALESCE(s.gross_sales_qty, 0),
    COALESCE(s.return_qty, 0),
    COALESCE(s.order_lines, 0)::integer,
    s.avg_unit_price,
    COALESCE(s.any_discount_flag, FALSE),
    EXTRACT(DOW FROM d.sale_date)::smallint,
    (EXTRACT(DOW FROM d.sale_date) IN (0, 6))
FROM dates d
CROSS JOIN products p
LEFT JOIN daily_sales s
  ON s.sale_date = d.sale_date AND s.product_id = p.product_id
"""


def main() -> int:
    print("schema", SCH)
    db = WecommDatabaseConnector()
    with db.engine.begin() as conn:
        for i, stmt in enumerate(DDL, 1):
            print(f"DDL {i}/{len(DDL)}...")
            conn.execute(text(stmt))
        print("INSERT (this can take a few minutes)...")
        conn.execute(text(INSERT_SQL))
        conn.execute(text("ANALYZE forecasting.daily_product_data"))

    summary = db.read_sql(
        """
        SELECT
          MIN(sale_date) AS first_date,
          MAX(sale_date) AS last_date,
          COUNT(*) AS rows,
          COUNT(DISTINCT sale_date) AS days,
          COUNT(DISTINCT product_id) AS products,
          SUM(CASE WHEN target_sales > 0 THEN 1 ELSE 0 END) AS product_days_with_sales,
          ROUND(SUM(target_sales)::numeric, 1) AS total_units
        FROM forecasting.daily_product_data
        """
    )
    print(summary.to_string(index=False))

    monthly = db.read_sql(
        """
        SELECT to_char(sale_date, 'YYYY-MM') AS month,
               ROUND(SUM(target_sales)::numeric, 1) AS units
        FROM forecasting.daily_product_data
        GROUP BY 1 ORDER BY 1
        """
    )
    print(monthly.to_string(index=False))

    print("Exporting CSV to", OUT)
    # stream in chunks to avoid huge memory
    # pandas read full then to_csv — ~600k-1M rows should be fine
    df = db.read_sql(
        """
        SELECT sale_date, product_id, upc, sku, product_name, category_id, category_name,
               list_price, purchase_price, is_scale, pack_size,
               target_sales, gross_sales_qty, return_qty, order_lines,
               avg_unit_price, any_discount_flag, dow, is_weekend
        FROM forecasting.daily_product_data
        ORDER BY product_id, sale_date
        """
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print("wrote", OUT, "rows", len(df), "mb", round(OUT.stat().st_size / 1e6, 2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
