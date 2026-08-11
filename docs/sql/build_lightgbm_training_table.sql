-- =============================================================================
-- Kevin / ChatGPT LightGBM training table (SALES-ONLY first version)
-- Tenant: wecomm_019fed0f-416b-7094-b228-794dc42270b6
-- Source: orders + order_items + products (+ optional categories / barcodes)
-- NO inventory (Kevin said not needed for LightGBM yet)
-- =============================================================================
-- HOW TO RUN (DBeaver / psql):
--   1) Connect to Wecomm Postgres (via SSH tunnel 127.0.0.1:5433)
--   2) Run this whole script
--   3) Validate with the SELECT checks at the bottom
--   4) Export table to CSV for Kevin (so it is not lost)
-- =============================================================================

-- Schema name is hardcoded below (DBeaver-safe; no \\set).
-- Tenant: wecomm_019fed0f-416b-7094-b228-794dc42270b6

CREATE SCHEMA IF NOT EXISTS forecasting;

DROP TABLE IF EXISTS forecasting.daily_product_data CASCADE;

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
    target_sales           numeric(15,4)  NOT NULL DEFAULT 0,  -- net units sold that day
    gross_sales_qty        numeric(15,4)  NOT NULL DEFAULT 0,
    return_qty             numeric(15,4)  NOT NULL DEFAULT 0,
    order_lines            integer        NOT NULL DEFAULT 0,
    avg_unit_price         numeric(15,4),
    any_discount_flag      boolean        NOT NULL DEFAULT false,
    dow                    smallint,      -- 0=Sunday .. 6=Saturday (Postgres EXTRACT DOW)
    is_weekend             boolean        NOT NULL DEFAULT false,
    created_at             timestamp      NOT NULL DEFAULT now(),
    PRIMARY KEY (sale_date, product_id)
);

CREATE INDEX IF NOT EXISTS idx_forecast_dpd_product_date
    ON forecasting.daily_product_data (product_id, sale_date);

CREATE INDEX IF NOT EXISTS idx_forecast_dpd_date
    ON forecasting.daily_product_data (sale_date);

-- -----------------------------------------------------------------------------
-- Build in one INSERT (sales-only, zero-filled)
-- Date range auto-detected from non-deleted, non-return orders
-- Assortment = all non-deleted products (zeros included)
-- -----------------------------------------------------------------------------
INSERT INTO forecasting.daily_product_data (
    sale_date,
    product_id,
    upc,
    sku,
    product_name,
    category_id,
    category_name,
    list_price,
    purchase_price,
    is_scale,
    pack_size,
    target_sales,
    gross_sales_qty,
    return_qty,
    order_lines,
    avg_unit_price,
    any_discount_flag,
    dow,
    is_weekend
)
WITH bounds AS (
    SELECT
        MIN(o.created_at)::date AS d0,
        MAX(o.created_at)::date AS d1
    FROM "wecomm_019fed0f-416b-7094-b228-794dc42270b6".orders o
    WHERE o.deleted_at IS NULL
      AND COALESCE(o.is_return, FALSE) = FALSE
),
dates AS (
    SELECT generate_series(b.d0, b.d1, INTERVAL '1 day')::date AS sale_date
    FROM bounds b
),
upc_one AS (
    -- one barcode per product (deterministic)
    SELECT DISTINCT ON (pb.product_id)
        pb.product_id,
        pb.barcode AS upc
    FROM "wecomm_019fed0f-416b-7094-b228-794dc42270b6".product_barcodes pb
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
    FROM "wecomm_019fed0f-416b-7094-b228-794dc42270b6".products p
    LEFT JOIN "wecomm_019fed0f-416b-7094-b228-794dc42270b6".categories c
        ON c.id = p.category_id
       AND c.deleted_at IS NULL
    LEFT JOIN upc_one u
        ON u.product_id = p.id
    WHERE p.deleted_at IS NULL
),
daily_sales AS (
    SELECT
        o.created_at::date AS sale_date,
        oi.product_id,
        SUM(GREATEST(COALESCE(oi.quantity, 0), 0)) AS gross_sales_qty,
        SUM(GREATEST(COALESCE(oi.returned_quantity, 0), 0)) AS return_qty,
        SUM(
            GREATEST(
                COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0),
                0
            )
        ) AS target_sales,
        COUNT(*) AS order_lines,
        CASE
            WHEN SUM(GREATEST(COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0), 0)) > 0
            THEN SUM(COALESCE(oi.total_price, 0))
                 / NULLIF(
                     SUM(GREATEST(COALESCE(oi.quantity, 0) - COALESCE(oi.returned_quantity, 0), 0)),
                     0
                   )
            ELSE NULL
        END AS avg_unit_price,
        BOOL_OR(
            COALESCE(oi.discount_amount, 0) > 0
            OR COALESCE(oi.total_allocated_discount, 0) > 0
            OR COALESCE(oi.total_allocated_promotion_discount, 0) > 0
        ) AS any_discount_flag
    FROM "wecomm_019fed0f-416b-7094-b228-794dc42270b6".order_items oi
    JOIN "wecomm_019fed0f-416b-7094-b228-794dc42270b6".orders o
      ON o.id = oi.order_id
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
    COALESCE(s.target_sales, 0) AS target_sales,
    COALESCE(s.gross_sales_qty, 0) AS gross_sales_qty,
    COALESCE(s.return_qty, 0) AS return_qty,
    COALESCE(s.order_lines, 0)::integer AS order_lines,
    s.avg_unit_price,
    COALESCE(s.any_discount_flag, FALSE) AS any_discount_flag,
    EXTRACT(DOW FROM d.sale_date)::smallint AS dow,
    (EXTRACT(DOW FROM d.sale_date) IN (0, 6)) AS is_weekend
FROM dates d
CROSS JOIN products p
LEFT JOIN daily_sales s
  ON s.sale_date = d.sale_date
 AND s.product_id = p.product_id
;

ANALYZE forecasting.daily_product_data;

-- =============================================================================
-- VALIDATION (run these and send results to Kevin)
-- =============================================================================

-- 1) Overall shape
SELECT
    MIN(sale_date) AS first_date,
    MAX(sale_date) AS last_date,
    COUNT(*) AS rows,
    COUNT(DISTINCT sale_date) AS days,
    COUNT(DISTINCT product_id) AS products,
    SUM(CASE WHEN target_sales > 0 THEN 1 ELSE 0 END) AS product_days_with_sales,
    ROUND(SUM(target_sales)::numeric, 1) AS total_units
FROM forecasting.daily_product_data;

-- Expected rough size:
--   products × days  (e.g. ~3000 products × ~212 days ≈ 600k+ rows)
-- days with calendar zeros included even if store closed some days

-- 2) Monthly units (should resemble source order_items)
SELECT
    to_char(sale_date, 'YYYY-MM') AS month,
    ROUND(SUM(target_sales)::numeric, 1) AS units,
    SUM(CASE WHEN target_sales > 0 THEN 1 ELSE 0 END) AS product_days_with_sales
FROM forecasting.daily_product_data
GROUP BY 1
ORDER BY 1;

-- 3) Spot-check one product
SELECT sale_date, target_sales, avg_unit_price, is_weekend
FROM forecasting.daily_product_data
WHERE product_id = (
    SELECT product_id
    FROM forecasting.daily_product_data
    GROUP BY product_id
    ORDER BY SUM(target_sales) DESC
    LIMIT 1
)
ORDER BY sale_date
LIMIT 40;

-- =============================================================================
-- EXPORT FOR KEVIN (pick one)
-- =============================================================================
-- A) DBeaver: right-click forecasting.daily_product_data → Export Data → CSV
-- B) psql:
-- \copy (SELECT * FROM forecasting.daily_product_data ORDER BY product_id, sale_date)
--   TO 'C:/Users/sreet/Desktop/forecasting_daily_product_data.csv' CSV HEADER
--
-- Send Kevin:
--   - CSV export
--   - first_date / last_date / row count / product count from validation #1
-- =============================================================================
