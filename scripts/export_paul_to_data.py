"""
Export Paul tenant tables into local data/ folders (map DB → files).

Requires SSH tunnel: 127.0.0.1:5433

  python scripts/export_paul_to_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from config.data_paths import (
    INVENTORY_DIR,
    PAST_INVOICES_DIR,
    SANDBOX_EXPORTS_DIR,
    SALES_DIR,
    VENDORS_DIR,
)
from config.settings import PROJECT_ROOT, get_settings
from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env", override=True)
    get_settings.cache_clear()

    schema = get_tenant_schema()
    sch = q_ident(schema)
    print("TENANT_SCHEMA", schema)

    db = WecommDatabaseConnector()

    # vendors
    VENDORS_DIR.mkdir(parents=True, exist_ok=True)
    vendors = db.read_sql(
        f"""
        SELECT id, name, email, phone, vendor_type, city, state, country, created_at
        FROM {sch}.vendors
        WHERE deleted_at IS NULL
        ORDER BY id
        """
    )
    vendors.to_csv(VENDORS_DIR / "vendors_from_paul.csv", index=False)
    print("vendors", len(vendors), "→ data/vendors/vendors_from_paul.csv")

    # products + vendor links
    products = db.read_sql(
        f"""
        SELECT id, sku, name, category_id, price, purchase_price, min_reorder_quantity,
               min_on_hand, is_active, has_expiration, created_at
        FROM {sch}.products
        WHERE deleted_at IS NULL
        ORDER BY id
        """
    )
    out_prod = SANDBOX_EXPORTS_DIR / "products"
    out_prod.mkdir(parents=True, exist_ok=True)
    products.to_csv(out_prod / "products.csv", index=False)
    print("products", len(products), "→ data/sandbox_exports/products/products.csv")

    pv = db.read_sql(
        f"""
        SELECT id, product_id, vendor_id, price, lead_time_days, created_at
        FROM {sch}.product_vendor
        ORDER BY id
        """
    )
    out_vp = SANDBOX_EXPORTS_DIR / "vendor_products"
    out_vp.mkdir(parents=True, exist_ok=True)
    pv.to_csv(out_vp / "product_vendor.csv", index=False)
    print("product_vendor", len(pv), "→ data/sandbox_exports/vendor_products/product_vendor.csv")

    # inventory
    INVENTORY_DIR.mkdir(parents=True, exist_ok=True)
    stock = db.read_sql(
        f"""
        SELECT
          pl.product_id,
          p.sku,
          p.name AS product_name,
          SUM(COALESCE(pl.quantity, 0)) AS quantity,
          MIN(pl.min_quantity) AS min_quantity,
          MAX(pl.max_quantity) AS max_quantity
        FROM {sch}.product_locations pl
        JOIN {sch}.products p ON p.id = pl.product_id
        WHERE pl.deleted_at IS NULL
        GROUP BY pl.product_id, p.sku, p.name
        ORDER BY pl.product_id
        """
    )
    stock.to_csv(INVENTORY_DIR / "current inventory count.csv", index=False)
    print("stock", len(stock), "→ data/inventory/current inventory count.csv")

    # sales (order lines flattened)
    SALES_DIR.mkdir(parents=True, exist_ok=True)
    sales = db.read_sql(
        f"""
        SELECT
          o.id AS order_id,
          o.created_at AS sale_datetime,
          DATE(o.created_at) AS sale_date,
          oi.product_id,
          p.sku,
          p.name AS product_name,
          GREATEST(COALESCE(oi.quantity,0) - COALESCE(oi.returned_quantity,0), 0) AS quantity,
          oi.price,
          o.status
        FROM {sch}.order_items oi
        JOIN {sch}.orders o ON o.id = oi.order_id
        JOIN {sch}.products p ON p.id = oi.product_id
        WHERE o.deleted_at IS NULL
          AND oi.deleted_at IS NULL
          AND COALESCE(o.is_return, FALSE) = FALSE
        ORDER BY o.created_at, oi.id
        """
    )
    sales.to_csv(SALES_DIR / "sales_from_paul.csv", index=False)
    print("sales lines", len(sales), "→ data/sales/sales_from_paul.csv")

    # past invoices / vendor POs
    PAST_INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    vos = db.read_sql(
        f"""
        SELECT
          vo.id AS vendor_order_id,
          vo.po_number,
          vo.vendor_id,
          v.name AS vendor_name,
          vo.status,
          vo.created_at,
          vo.exp_fulfillment_date,
          vop.product_id,
          p.name AS product_name,
          vop.quantity,
          vop.fulfilled_quantity
        FROM {sch}.vendor_orders vo
        JOIN {sch}.vendors v ON v.id = vo.vendor_id
        LEFT JOIN {sch}.vendor_order_products vop ON vop.vendor_order_id = vo.id
        LEFT JOIN {sch}.products p ON p.id = vop.product_id
        ORDER BY vo.created_at, vop.id
        """
    )
    vos.to_csv(PAST_INVOICES_DIR / "vendor_orders_from_paul.csv", index=False)
    print("vendor order lines", len(vos), "→ data/Past Invoices/vendor_orders_from_paul.csv")

    print("DONE — local data/ folders mapped from Paul.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
