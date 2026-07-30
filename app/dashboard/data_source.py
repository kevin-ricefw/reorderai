"""Dashboard data source — sandbox PostgreSQL with CSV fallback."""

from __future__ import annotations

from database.readers.sandbox_data_reader import sandbox_db_available


def data_source_label() -> str:
    return "PostgreSQL sandbox" if sandbox_db_available() else "CSV / Excel files"


def using_sandbox_db() -> bool:
    return sandbox_db_available()
