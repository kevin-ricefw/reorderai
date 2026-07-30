"""Sandbox table naming — same names as production (no prefix)."""

SANDBOX_SCHEMA = "dbo"


def sandbox_table_name(production_name: str) -> str:
    """Return the production table name unchanged."""
    name = production_name.strip()
    if name.lower().startswith("ai_"):
        return name[3:]
    return name


# Backward-compatible alias
to_ai_table_name = sandbox_table_name


def is_legacy_ai_table(name: str) -> bool:
    return name.lower().startswith("ai_")
