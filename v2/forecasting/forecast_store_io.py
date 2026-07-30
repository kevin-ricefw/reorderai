"""Read/write Phase-1 forecast_store artifacts (parquet/csv under data/forecast_store)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from config.data_paths import PROJECT_ROOT

FORECAST_STORE_DIR = PROJECT_ROOT / "data" / "forecast_store"
LATEST_FORECASTS = FORECAST_STORE_DIR / "latest_forecasts.parquet"
LATEST_CLASSIFICATIONS = FORECAST_STORE_DIR / "latest_classifications.parquet"
LATEST_FORECASTS_CSV = FORECAST_STORE_DIR / "latest_forecasts.csv"
LATEST_CLASSIFICATIONS_CSV = FORECAST_STORE_DIR / "latest_classifications.csv"
META_PATH = FORECAST_STORE_DIR / "latest_meta.txt"


def ensure_dir() -> Path:
    FORECAST_STORE_DIR.mkdir(parents=True, exist_ok=True)
    return FORECAST_STORE_DIR


def save_forecast_store(
    classifications: pd.DataFrame,
    forecasts: pd.DataFrame,
    *,
    as_of: str,
) -> Path:
    out = ensure_dir()
    # Parquet preferred; CSV always written for easy inspection
    try:
        classifications.to_parquet(LATEST_CLASSIFICATIONS, index=False)
        forecasts.to_parquet(LATEST_FORECASTS, index=False)
    except Exception:
        pass
    classifications.to_csv(LATEST_CLASSIFICATIONS_CSV, index=False)
    forecasts.to_csv(LATEST_FORECASTS_CSV, index=False)
    META_PATH.write_text(as_of + "\n", encoding="utf-8")
    return out


def load_latest_forecasts() -> pd.DataFrame | None:
    if LATEST_FORECASTS.exists():
        try:
            return pd.read_parquet(LATEST_FORECASTS)
        except Exception:
            pass
    if LATEST_FORECASTS_CSV.exists():
        return pd.read_csv(LATEST_FORECASTS_CSV)
    return None


def load_latest_classifications() -> pd.DataFrame | None:
    if LATEST_CLASSIFICATIONS.exists():
        try:
            return pd.read_parquet(LATEST_CLASSIFICATIONS)
        except Exception:
            pass
    if LATEST_CLASSIFICATIONS_CSV.exists():
        return pd.read_csv(LATEST_CLASSIFICATIONS_CSV)
    return None
