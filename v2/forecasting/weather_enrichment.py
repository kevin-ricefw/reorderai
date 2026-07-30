"""Okemos, Michigan weather enrichment for sales trend analysis."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd

# Okemos, MI (near East Lansing)
OKEMOS_LAT = 42.7223
OKEMOS_LON = -84.4274
OKEMOS_TZ = "America/Detroit"

WMO_WEATHER_LABELS: dict[int, str] = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    95: "Thunderstorm",
}


from config.data_paths import WEATHER_CACHE_DIR


def _cache_path() -> Path:
    WEATHER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return WEATHER_CACHE_DIR / "okemos_weather_2026.json"


def _forecast_cache_path() -> Path:
    WEATHER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return WEATHER_CACHE_DIR / "okemos_weather_forecast.json"


def _daily_payload_to_df(daily: dict) -> pd.DataFrame:
    dates = pd.to_datetime(daily.get("time", []))
    if len(dates) == 0:
        return pd.DataFrame(
            columns=["date", "temp_max_f", "temp_min_f", "precip_in", "weather_label", "weather_code"]
        )

    df = pd.DataFrame(
        {
            "date": dates.normalize(),
            "temp_max_c": daily.get("temperature_2m_max", []),
            "temp_min_c": daily.get("temperature_2m_min", []),
            "precip_mm": daily.get("precipitation_sum", []),
            "weather_code": daily.get("weathercode", []),
        }
    )
    df["temp_max_f"] = df["temp_max_c"] * 9 / 5 + 32
    df["temp_min_f"] = df["temp_min_c"] * 9 / 5 + 32
    df["precip_in"] = df["precip_mm"] / 25.4
    df["weather_label"] = df["weather_code"].apply(
        lambda c: WMO_WEATHER_LABELS.get(int(c), "Unknown") if pd.notna(c) else "Unknown"
    )
    return df.drop(columns=["temp_max_c", "temp_min_c", "precip_mm"])


def _fetch_open_meteo(start: date, end: date) -> dict:
    params = urllib.parse.urlencode(
        {
            "latitude": OKEMOS_LAT,
            "longitude": OKEMOS_LON,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
            "timezone": OKEMOS_TZ,
        }
    )
    url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _fetch_open_meteo_forecast(*, forecast_days: int = 7) -> dict:
    params = urllib.parse.urlencode(
        {
            "latitude": OKEMOS_LAT,
            "longitude": OKEMOS_LON,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
            "timezone": OKEMOS_TZ,
            "forecast_days": min(max(forecast_days, 1), 16),
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


def load_okemos_weather(
    start: date,
    end: date,
    *,
    use_cache: bool = True,
) -> pd.DataFrame:
    """Daily weather for Okemos, MI (Open-Meteo archive + local cache)."""
    cache = _cache_path()
    cached: dict[str, dict] = {}
    if use_cache and cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))

    key = f"{start.isoformat()}_{end.isoformat()}"
    if key not in cached:
        payload = _fetch_open_meteo(start, end)
        cached[key] = payload.get("daily", {})
        cache.write_text(json.dumps(cached, indent=2), encoding="utf-8")

    return _daily_payload_to_df(cached[key])


def load_okemos_weather_forecast(
    *,
    forecast_days: int = 7,
    use_cache: bool = True,
    cache_ttl_hours: int = 3,
) -> pd.DataFrame:
    """Next N days of Okemos weather from Open-Meteo forecast API."""
    cache = _forecast_cache_path()
    today = date.today()
    key = f"{today.isoformat()}_{forecast_days}d"

    if use_cache and cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        entry = cached.get(key)
        if entry:
            fetched_at = pd.Timestamp(entry.get("fetched_at", "1970-01-01"))
            if (pd.Timestamp.now() - fetched_at).total_seconds() < cache_ttl_hours * 3600:
                return _daily_payload_to_df(entry.get("daily", {}))

    payload = _fetch_open_meteo_forecast(forecast_days=forecast_days)
    daily = payload.get("daily", {})
    if use_cache:
        cached = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        cached[key] = {"fetched_at": pd.Timestamp.now().isoformat(), "daily": daily}
        cache.write_text(json.dumps(cached, indent=2), encoding="utf-8")

    return _daily_payload_to_df(daily)


def merge_weather_features(
    df: pd.DataFrame,
    start: date,
    end: date,
    date_col: str = "date",
) -> pd.DataFrame:
    """Join Okemos weather onto sales rows by date."""
    if df.empty or date_col not in df.columns:
        return df
    weather = load_okemos_weather(start, end)
    if weather.empty:
        return df
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col], errors="coerce").dt.normalize()
    return out.merge(weather, on="date", how="left")
