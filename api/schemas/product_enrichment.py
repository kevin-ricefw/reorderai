"""Product enrichment (product_name / brand / size) schemas."""

from __future__ import annotations

from pydantic import BaseModel


class ProductEnrichRequest(BaseModel):
    id: int | None = None
    limit: int | None = None


class ProductEnrichResult(BaseModel):
    id: int
    product_name: str
    brand: str
    size: str


class ProductEnrichBatchResponse(BaseModel):
    updated: int
    items: list[ProductEnrichResult]
    failed: list[dict]
