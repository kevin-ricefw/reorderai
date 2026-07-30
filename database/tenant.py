"""Resolve Wecomm tenant schema for SQL queries."""

from __future__ import annotations

import os


DEFAULT_PAUL_SCHEMA = "wecomm_019fafca-fa67-7393-84c4-4ec423f88c15"


def get_tenant_schema() -> str:
    """
    Tenant business tables live in schema wecomm_<uuid>.

    Set TENANT_SCHEMA in .env. Falls back to Paul schema for local testing.
    """
    raw = (os.getenv("TENANT_SCHEMA") or "").strip().strip('"').strip("'")
    return raw or DEFAULT_PAUL_SCHEMA


def q_ident(name: str) -> str:
    """Quote a Postgres identifier (schema/table)."""
    return '"' + name.replace('"', '""') + '"'
