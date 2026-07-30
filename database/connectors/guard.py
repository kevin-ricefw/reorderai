"""Read-only query guard for production SQL Server."""

from __future__ import annotations

import re


class ReadOnlyViolationError(Exception):
    pass


_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|MERGE|EXEC|EXECUTE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class ReadOnlyQueryGuard:
    @staticmethod
    def validate(query: str) -> None:
        stripped = query.strip().rstrip(";")
        if not stripped:
            raise ReadOnlyViolationError("Empty query")
        if _FORBIDDEN.search(stripped):
            raise ReadOnlyViolationError("Only SELECT queries are allowed on production")
        head = stripped.lstrip("(").upper()
        if not (head.startswith("SELECT") or head.startswith("WITH")):
            raise ReadOnlyViolationError("Query must start with SELECT or WITH")
