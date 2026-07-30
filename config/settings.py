"""
Centralized application settings.

Loads from config/settings.yaml with environment variable overrides.
Single Wecomm Azure PostgreSQL database — no sandbox / legacy prod split.

DB_* values are read directly from the environment (.env) so passwords with
@, #, `, quotes, etc. are not mangled by YAML placeholder substitution.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env", override=True)

logger = logging.getLogger(__name__)


def _strip_wrapping_quotes(value: str) -> str:
    """Remove a single layer of matching ' or \" wrappers from .env values."""
    s = (value or "").strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _resolve_env_vars(value: Any) -> Any:
    """Replace ${VAR:default} placeholders with environment values."""
    if isinstance(value, str):

        def replacer(match: re.Match[str]) -> str:
            var_name, default = match.group(1), match.group(2)
            return os.getenv(var_name, default or "")

        return re.sub(r"\$\{([^:}]+)(?::([^}]*))?\}", replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_yaml_config() -> dict[str, Any]:
    config_path = CONFIG_DIR / "settings.yaml"
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _resolve_env_vars(raw or {})


def _db_from_environ() -> dict[str, Any]:
    """
    Authoritative DB settings from process env (.env via load_dotenv).

    Prefer this over YAML ${...} expansion so special characters in
    DB_PASSWORD (#, @, `, quotes) stay intact.
    """
    out: dict[str, Any] = {}
    mapping = {
        "host": "DB_HOST",
        "port": "DB_PORT",
        "database": "DB_DATABASE",
        "username": "DB_USERNAME",
        "password": "DB_PASSWORD",
        "sslmode": "DB_SSLMODE",
    }
    for field, env_key in mapping.items():
        if env_key not in os.environ:
            continue
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        if field == "password":
            out[field] = _strip_wrapping_quotes(raw)
        elif field == "port":
            try:
                out[field] = int(str(raw).strip())
            except ValueError:
                out[field] = raw
        else:
            out[field] = _strip_wrapping_quotes(str(raw))
    # Optional dialect hint
    conn = (os.getenv("DB_CONNECTION") or "").strip().lower()
    if conn in {"pgsql", "postgres", "postgresql"}:
        out["dialect"] = "postgresql"
    return out


class DatabaseSettings(BaseSettings):
    dialect: str = "postgresql"
    driver: str = "postgresql"
    host: str = "127.0.0.1"
    port: int = 5433
    database: str = "postgres"
    username: str = ""
    password: str = ""
    sslmode: str = "require"
    read_only: bool = False
    connection_timeout: int = 30
    query_timeout: int = 120

    # Do NOT auto-pull random env names; we inject DB_* explicitly in from_yaml
    model_config = SettingsConfigDict(extra="ignore", env_prefix="__UNUSED_DB_")

    @field_validator("password", "username", "host", "database", "sslmode", mode="before")
    @classmethod
    def _clean_str(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _strip_wrapping_quotes(v)
        return v

    @property
    def is_postgresql(self) -> bool:
        return self.dialect.lower() in ("postgresql", "postgres", "pgsql")

    @property
    def password_loaded(self) -> bool:
        return bool(self.password)

    @property
    def password_length(self) -> int:
        return len(self.password or "")

    def safe_debug(self) -> dict[str, Any]:
        """Safe connection diagnostics — never includes the password value."""
        pwd = self.password or ""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "username": self.username,
            "sslmode": self.sslmode,
            "dialect": self.dialect,
            "password_exists": bool(pwd),
            "password_length": len(pwd),
            "password_has_at": "@" in pwd,
            "password_has_hash": "#" in pwd,
            "password_has_backtick": "`" in pwd,
            "password_has_wrapping_quotes": (
                len(pwd) >= 2 and pwd[0] == pwd[-1] and pwd[0] in ("'", '"')
            ),
        }

    def sqlalchemy_url_obj(self) -> URL:
        """
        Build a SQLAlchemy URL with password as a discrete field.

        Avoids manual quote_plus string assembly, which is easy to get wrong
        for passwords containing @, #, %, `, etc.
        """
        return URL.create(
            "postgresql+psycopg2",
            username=self.username.strip(),
            password=self.password,  # may contain @ # ` — URL.create encodes safely
            host=self.host.strip(),
            port=int(self.port),
            database=self.database.strip(),
            query={"sslmode": (self.sslmode or "require").strip()},
        )

    @property
    def sqlalchemy_url(self) -> str:
        """String form (password redacted if rendered via hide_password)."""
        return self.sqlalchemy_url_obj().render_as_string(hide_password=False)

    def connect_args(self) -> dict[str, Any]:
        """Explicit libpq kwargs — used as a second channel for password/ssl."""
        return {
            "connect_timeout": int(self.connection_timeout),
            "sslmode": (self.sslmode or "require").strip(),
        }


class AppSettings(BaseSettings):
    name: str = "Inventory AI Reorder Prediction System"
    environment: str = "development"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(extra="ignore")


class Settings(BaseSettings):
    """Root settings — one database only."""

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    synthetic_data: dict[str, Any] = Field(default_factory=dict)
    feature_engineering: dict[str, Any] = Field(default_factory=dict)
    ml: dict[str, Any] = Field(default_factory=dict)
    prediction_engine: dict[str, Any] = Field(default_factory=dict)
    api: dict[str, Any] = Field(default_factory=dict)
    dashboard: dict[str, Any] = Field(default_factory=dict)
    v2: dict[str, Any] = Field(default_factory=dict)

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def sandbox_database(self) -> DatabaseSettings:
        return self.database

    @property
    def production_database(self) -> DatabaseSettings:
        return self.database

    @classmethod
    def from_yaml(cls) -> Settings:
        # Always refresh .env before building settings (API may start before edits)
        load_dotenv(PROJECT_ROOT / ".env", override=True)

        data = load_yaml_config()
        db_raw = dict(data.get("database") or {})
        if not db_raw and data.get("sandbox_database"):
            db_raw = dict(data["sandbox_database"])

        # DB_* from .env wins (correct handling of @ # ` in password)
        db_raw.update(_db_from_environ())

        database = DatabaseSettings(**db_raw)
        logger.info("Database settings loaded: %s", database.safe_debug())

        return cls(
            app=AppSettings(**data.get("app", {})),
            database=database,
            synthetic_data=data.get("synthetic_data", {}),
            feature_engineering=data.get("feature_engineering", {}),
            ml=data.get("ml", {}),
            prediction_engine=data.get("prediction_engine", {}),
            api=data.get("api", {}),
            dashboard=data.get("dashboard", {}),
            v2=data.get("v2", {}),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_yaml()
