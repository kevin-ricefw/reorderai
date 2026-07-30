"""Health endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "reorder-ai"}


@router.get("/db-health")
async def db_health() -> dict:
    """Check Wecomm Azure Postgres connectivity (no secrets returned)."""
    from dotenv import load_dotenv

    from config.settings import PROJECT_ROOT, get_settings
    from database.connectors.wecomm import WecommDatabaseConnector

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    get_settings.cache_clear()
    db = get_settings().database
    debug = db.safe_debug()

    info = {
        "configured": bool(db.host and db.username and db.password),
        "host": debug["host"],
        "port": debug["port"],
        "database": debug["database"],
        "username": debug["username"],
        "sslmode": debug["sslmode"],
        "ok": False,
        "error": None,
        "table_count": None,
    }

    if not info["configured"]:
        info["error"] = "DB_* settings missing in .env"
        return info

    try:
        conn = WecommDatabaseConnector()
        with conn.engine.connect() as c:
            n = c.exec_driver_sql(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
            ).scalar()
        info["ok"] = True
        info["table_count"] = int(n or 0)
    except Exception as exc:  # noqa: BLE001 — surface connection errors to client
        info["error"] = f"{type(exc).__name__}: {exc}"

    return info
