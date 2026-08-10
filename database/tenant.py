"""Resolve Wecomm tenant schema for SQL queries."""

from __future__ import annotations

import os


DEFAULT_PAUL_SCHEMA = "wecomm_019fafca-fa67-7393-84c4-4ec423f88c15"


def get_tenant_schema(tenant_id: str | None = None) -> str:
    """
    Tenant business tables live in schema wecomm_<uuid>.

    tenant_id, when passed, overrides the schema for this call only (e.g. a
    per-request tenant selected via the API). Otherwise falls back to
    TENANT_SCHEMA in .env, then the Paul schema for local testing.
    """
    if tenant_id:
        return tenant_id.strip().strip('"').strip("'")
    raw = (os.getenv("TENANT_SCHEMA") or "").strip().strip('"').strip("'")
    return raw or DEFAULT_PAUL_SCHEMA


def q_ident(name: str) -> str:
    """Quote a Postgres identifier (schema/table)."""
    return '"' + name.replace('"', '""') + '"'
