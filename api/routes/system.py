"""Health and analytics output endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config.data_paths import PROJECT_ROOT

router = APIRouter(prefix="/api", tags=["system"])

OUTPUT_DIR = PROJECT_ROOT / "outputs" / "analytics"

ALLOWED_OUTPUTS = {
    "sku_sales_metrics.csv",
    "sku_rankings_top100.csv",
    "top_100_products.csv",
    "model_comparison.csv",
    "model_comparison_winners.csv",
    "model_evaluation.csv",
    "sku_demand_forecasts.csv",
    "sku_reorder_recommendations.csv",
    "sku_master_analysis.csv",
    "order_now_list.csv",
    "analysis_summary.json",
    "syntetos_boylan_demand_patterns.csv",
    "feature_correlation_matrix.csv",
    "candidate_feature_correlation_matrix.csv",
}


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "inventory-ai"}


@router.get("/db-health")
async def db_health() -> dict:
    """Check Wecomm Azure Postgres connectivity (no secrets returned)."""
    from pathlib import Path

    from dotenv import dotenv_values

    from config.settings import PROJECT_ROOT, get_settings

    get_settings.cache_clear()
    # Force reload .env with override so stale process env cannot win
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    get_settings.cache_clear()
    db = get_settings().database
    debug = db.safe_debug()

    env_path = PROJECT_ROOT / ".env"
    file_vals = dotenv_values(env_path) if env_path.exists() else {}
    file_pwd = file_vals.get("DB_PASSWORD") or ""
    if len(file_pwd) >= 2 and file_pwd[0] == file_pwd[-1] and file_pwd[0] in "\"'":
        file_pwd = file_pwd[1:-1]

    info = {
        "configured": bool(db.host and db.username and db.password),
        "env_file": str(env_path),
        "host": debug["host"],
        "port": debug["port"],
        "database": debug["database"],
        "username": debug["username"],
        "sslmode": debug["sslmode"],
        "dialect": debug["dialect"],
        "password_exists": debug["password_exists"],
        "password_length": debug["password_length"],
        "password_has_at": debug["password_has_at"],
        "password_has_hash": debug["password_has_hash"],
        "password_has_backtick": debug["password_has_backtick"],
        "password_has_wrapping_quotes": debug["password_has_wrapping_quotes"],
        "dotenv_password_length": len(file_pwd),
        "settings_password_matches_dotenv_length": len(file_pwd) == debug["password_length"],
        "ok": False,
        "error": None,
        "table_count": None,
        "diagnosis": None,
    }
    if not info["configured"]:
        info["error"] = "DB_HOST / DB_USERNAME / DB_PASSWORD missing in .env"
        info["diagnosis"] = "missing_config"
        return info
    if info["password_length"] == 0:
        info["error"] = "DB_PASSWORD loaded empty — check .env quoting around special characters"
        info["diagnosis"] = "empty_password"
        return info
    if not info["password_has_hash"] or not info["password_has_at"]:
        info["warning"] = (
            "Password may be truncated by .env parsing "
            "(expected @ and # in DB_PASSWORD). Keep DB_PASSWORD in double quotes."
        )
        info["diagnosis"] = "possible_dotenv_truncation"
    else:
        info["env_parse_ok"] = True

    try:
        from database.connectors.wecomm import WecommDatabaseConnector

        c = WecommDatabaseConnector(db)
        ping = c.ping()
        tables = c.list_tables()
        info["ok"] = True
        info["diagnosis"] = "connected"
        info["connected_as"] = ping.get("username")
        info["connected_database"] = ping.get("database")
        info["table_count"] = int(len(tables))
        info["tables_sample"] = (
            tables.head(30).to_dict(orient="records") if not tables.empty else []
        )
    except Exception as exc:  # noqa: BLE001
        err = str(exc).split("\n")[0][:300]
        info["ok"] = False
        info["error"] = err
        if "password authentication failed" in err.lower():
            info["diagnosis"] = "server_rejected_credentials"
            info["exact_reason"] = (
                "SSH tunnel and app config are working. PostgreSQL actively rejected "
                "user 'wecomm_admin' with the password currently in .env. "
                "This is not a host/port/SSL/dotenv bug. "
                "Ask your TL for the current correct DB password (or username format)."
            )
        else:
            info["diagnosis"] = "connection_error"
    return info


@router.get("/outputs")
async def list_outputs() -> dict:
    files = []
    if OUTPUT_DIR.exists():
        for name in sorted(ALLOWED_OUTPUTS):
            path = OUTPUT_DIR / name
            if path.exists():
                files.append(
                    {
                        "name": name,
                        "size_bytes": path.stat().st_size,
                        "url": f"/api/outputs/{name}",
                    }
                )
    summary = {}
    summary_path = OUTPUT_DIR / "analysis_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {"files": files, "summary": summary}


@router.get("/outputs/{filename}")
async def download_output(filename: str) -> FileResponse:
    if filename not in ALLOWED_OUTPUTS:
        raise HTTPException(status_code=404, detail="Unknown output file.")
    path = OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not generated yet. Run Train Now first.")
    media = "application/json" if filename.endswith(".json") else "text/csv"
    return FileResponse(path, media_type=media, filename=filename)
