"""
DB reads/writes for products.product_name / brand / size enrichment.

Tenant schema products table: id, name, sku, product_name, brand, size, ...
"""

from __future__ import annotations

from typing import Any

from database.connectors.wecomm import WecommDatabaseConnector
from database.tenant import get_tenant_schema, q_ident

_MISSING = "(col IS NULL OR TRIM(col) = '')"


class ProductRepository:
    def __init__(self, tenant_id: str | None = None) -> None:
        self.schema = get_tenant_schema(tenant_id)
        self._db: WecommDatabaseConnector | None = None

    def _conn(self) -> WecommDatabaseConnector:
        if self._db is None:
            self._db = WecommDatabaseConnector()
        return self._db

    def get_by_id(self, product_id: int) -> dict[str, Any] | None:
        sch = q_ident(self.schema)
        df = self._conn().read_sql(
            f"""
            SELECT id, name, product_name, brand, size
            FROM {sch}.products
            WHERE id = :id
            """,
            {"id": product_id},
        )
        if df.empty:
            return None
        return df.iloc[0].to_dict()

    def find_missing(self, limit: int | None = None) -> list[dict[str, Any]]:
        sch = q_ident(self.schema)
        missing = " OR ".join(
            _MISSING.replace("col", c) for c in ("product_name", "brand", "size")
        )
        sql = f"""
            SELECT id, name
            FROM {sch}.products
            WHERE {missing}
            ORDER BY id
        """
        if limit:
            sql += " LIMIT :limit"
        df = self._conn().read_sql(sql, {"limit": limit} if limit else {})
        return df.to_dict(orient="records")

    def update_fields(self, product_id: int, product_name: str, brand: str, size: str) -> None:
        sch = q_ident(self.schema)
        self._conn().execute(
            f"""
            UPDATE {sch}.products
            SET product_name = :product_name, brand = :brand, size = :size
            WHERE id = :id
            """,
            {"product_name": product_name, "brand": brand, "size": size, "id": product_id},
        )
