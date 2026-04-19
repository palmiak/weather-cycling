#!/usr/bin/env python3
"""
🚴 Cycling Weather CLI
Aggregates forecasts from multiple sources and summarizes with AI.
"""

import asyncio
import aiohttp
import json
import sys
import os
import argparse
from datetime import datetime, timedelta, date
from typing import Optional
import math
from dotenv import load_dotenv

load_dotenv()

# ── ANSI colors ────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
WHITE = "\033[97m"
GRAY = "\033[90m"

# ── Config ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
OWM_API_KEY = os.environ.get("OWM_API_KEY", "")        # openweathermap.org (free)
TOMORROW_API_KEY = os.environ.get("TOMORROW_API_KEY", "")  # tomorrow.io (free tier)


# ══════════════════════════════════════════════════════════════════════════════
# GEOCODING — resolve city name → lat/lon
# ══════════════════════════════════════════════════════════════════════════════

async def geocode(session: aiohttp.ClientSession, location: str) -> tuple[float, float, str]:
    """Returns (lat, lon, display_name). Prompts to pick when multiple results match."""
    # Direct coordinates — skip geocoding
    parts = location.split(",")
    if len(parts) == 2:
        try:
            return float(parts[0]), float(parts[1]), location
        except ValueError:
            pass

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": location, "format": "json", "limit": 5,
              "addressdetails": 1, "featuretype": "settlement"}
    headers = {"User-Agent": "CyclingWeatherCLI/1.0"}
    async with session.get(url, params=params, headers=headers) as r:
        data = await r.json()
    if not data:
        raise ValueError(f"Location not found: {location}")

    if len(data) == 1:
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        name = data[0].get("display_name", location)
        return lat, lon, name

    # Multiple results — let the user pick
    print(f"\n{YELLOW}Multiple places match {location!r}. Pick one:{RESET}")
    for i, r in enumerate(data, 1):
        addr = r.get("address", {})
        county  = addr.get("county", "")
        state   = addr.get("state", "")
        country = addr.get("country_code", "").upper()
        region  = ", ".join(p for p in [county, state, country] if p)
        lat_r, lon_r = float(r["lat"]), float(r["lon"])
        print(f"  {BOLD}{i}{RESET}. {r.get('name', r.get('display_name', ''))} — {region}  {DIM}({lat_r:.4f}, {lon_r:.4f}){RESET}")

    print()
    while True:
        try:
            choice = input(f"Enter number [1-{len(data)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(data):
                break
        except (ValueError, EOFError):
            pass
        print(f"{RED}Invalid choice. Enter a number between 1 and {len(data)}.{RESET}")

    chosen = data[idx]
    return float(chosen["lat"]), float(chosen["lon"]), chosen.get("display_name", location)


# ══════════════════════════════════════════════════════════════════════════════
# SOURCES
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_open_meteo(session: aiohttp.ClientSession, lat: float, lon: float, day_offset: int = 0) -> dict:
    """Open-Meteo — free, no key, very detailed."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation,precipitation_probability,windspeed_10m,windgusts_10m,weathercode,relativehumidity_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,windgusts_10m_max,weathercode",
        "current_weather": "true",
        "forecast_days": 3,
        "timezone": "auto",
        "windspeed_unit": "kmh",
    }
    async with session.get(url, params=params) as r:
        data = await r.json()

    current = data.get("current_weather", {})
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})

    hour_start = day_offset * 24
    times = hourly.get("time", [])[hour_start:hour_start + 24]
    temps = hourly.get("temperature_2m", [])[hour_start:hour_start + 24]
    precip = hourly.get("precipitation", [])[hour_start:hour_start + 24]
    precip_prob = hourly.get("precipitation_probability", [])[hour_start:hour_start + 24]
    wind = hourly.get("windspeed_10m", [])[hour_start:hour_start + 24]
    gusts = hourly.get("windgusts_10m", [])[hour_start:hour_start + 24]
    humidity = hourly.get("relativehumidity_2m", [])[hour_start:hour_start + 24]
    codes = hourly.get("weathercode", [])[hour_start:hour_start + 24]

    hourly_detail = [
        {
            "time": t[11:16] if t else "",   # "2026-04-18T06:00" → "06:00"
            "temp_c": temps[i] if i < len(temps) else None,
            "precip_mm": precip[i] if i < len(precip) else None,
            "precip_prob_pct": precip_prob[i] if i < len(precip_prob) else None,
            "wind_kmh": wind[i] if i < len(wind) else None,
            "weathercode": codes[i] if i < len(codes) else None,
        }
        for i, t in enumerate(times)
    ]

    result = {
        "source": "Open-Meteo",
        "url": "https://open-meteo.com",
        "utc_offset_seconds": data.get("utc_offset_seconds", 0),
        "hourly_detail": hourly_detail,
        "next24h": {
            "temp_min": min(temps) if temps else None,
            "temp_max": max(temps) if temps else None,
            "total_precip_mm": round(sum(precip), 1) if precip else None,
            "avg_wind_kmh": round(sum(wind) / len(wind), 1) if wind else None,
            "max_gust_kmh": max(gusts) if gusts else None,
            "avg_humidity_pct": round(sum(humidity) / len(humidity)) if humidity else None,
        },
    }

    if day_offset == 0:
        result["current_temp_c"] = current.get("temperature")
        result["current_wind_kmh"] = current.get("windspeed")
        result["current_winddir_deg"] = current.get("winddirection")
        result["weathercode"] = current.get("weathercode")
        result["tomorrow"] = {
            "temp_min": daily["temperature_2m_min"][1] if len(daily.get("temperature_2m_min", [])) > 1 else None,
            "temp_max": daily["temperature_2m_max"][1] if len(daily.get("temperature_2m_max", [])) > 1 else None,
            "precip_mm": daily["precipitation_sum"][1] if len(daily.get("precipitation_sum", [])) > 1 else None,
            "max_wind_kmh": daily["windspeed_10m_max"][1] if len(daily.get("windspeed_10m_max", [])) > 1 else None,
            "max_gust_kmh": daily["windgusts_10m_max"][1] if len(daily.get("windgusts_10m_max", [])) > 1 else None,
        }

    return result


async def fetch_wttr(session: aiohttp.ClientSession, lat: float, lon: float, day_offset: int = 0) -> dict:
    """wttr.in — free weather API with ASCII art data."""
    url = f"https://wttr.in/{lat},{lon}"
    params = {"format": "j1"}
    async with session.get(url, params=params) as r:
        data = await r.json(content_type=None)

    current = data["current_condition"][0]
    weather = data["weather"]

    result = {
        "source": "wttr.in",
        "url": "https://wttr.in",
        "next24h": {},
        "tomorrow": {},
    }

    if day_offset == 0:
        result["current_temp_c"] = int(current.get("temp_C", 0))
        result["current_feels_like_c"] = int(current.get("FeelsLikeC", 0))
        result["current_humidity_pct"] = int(current.get("humidity", 0))
        result["current_wind_kmh"] = int(current.get("windspeedKmph", 0))
        result["current_winddir_deg"] = int(current.get("winddirDegree", 0))
        result["current_visibility_km"] = int(current.get("visibility", 0))
        result["current_desc"] = current.get("weatherDesc", [{}])[0].get("value", "")

    if len(weather) > day_offset:
        target = weather[day_offset]
        result["next24h"] = {
            "temp_min": int(target.get("mintempC", 0)),
            "temp_max": int(target.get("maxtempC", 0)),
            "total_precip_mm": sum(float(h.get("precipMM", 0)) for h in target.get("hourly", [])),
            "avg_wind_kmh": round(sum(int(h.get("windspeedKmph", 0)) for h in target.get("hourly", [])) / max(len(target.get("hourly", [])), 1), 1),
            "sunrise": target.get("astronomy", [{}])[0].get("sunrise", ""),
            "sunset": target.get("astronomy", [{}])[0].get("sunset", ""),
        }

    if day_offset == 0 and len(weather) > 1:
        tom = weather[1]
        result["tomorrow"] = {
            "temp_min": int(tom.get("mintempC", 0)),
            "temp_max": int(tom.get("maxtempC", 0)),
            "total_precip_mm": sum(float(h.get("precipMM", 0)) for h in tom.get("hourly", [])),
            "avg_wind_kmh": round(sum(int(h.get("windspeedKmph", 0)) for h in tom.get("hourly", [])) / max(len(tom.get("hourly", [])), 1), 1),
        }

    return result


async def fetch_imgw_warnings(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch active IMGW meteorological warnings. Returns [] when none active."""
    url = "https://danepubliczne.imgw.pl/api/data/warningsmeteo"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            text = await r.text()
            text = text.strip()
            if text == "0" or not text:
                return []
            data = await r.json(content_type=None)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
    except Exception:
        pass
    return []


async def fetch_imgw(session: aiohttp.ClientSession, lat: float, lon: float, day_offset: int = 0) -> dict:
    """
    IMGW — Polish national meteorological institute.
    Uses the public observations + forecast API.
    Falls back to nearest station data.
    """
    if day_offset > 0:
        return {
            "source": "IMGW (Polish Met Office)",
            "url": "https://danepubliczne.imgw.pl",
            "note": "IMGW provides real-time observations only — no multi-day forecast available",
        }

    stations_url = "https://danepubliczne.imgw.pl/api/data/synop"
    async with session.get(stations_url) as r:
        stations = await r.json()

    def dist(s):
        try:
            slat = float(s.get("stacja_lat") or s.get("lat") or 0)
            slon = float(s.get("stacja_lon") or s.get("lon") or 0)
            return math.sqrt((slat - lat)**2 + (slon - lon)**2)
        except Exception:
            return 99999

    nearest = min(stations, key=dist)
    d = nearest

    return {
        "source": "IMGW (Polish Met Office)",
        "url": "https://danepubliczne.imgw.pl",
        "station_name": d.get("stacja", "unknown"),
        "current_temp_c": _safe_float(d.get("temperatura")),
        "current_wind_kmh": _safe_float(d.get("predkosc_wiatru"), multiply=3.6),  # m/s → km/h
        "current_winddir_deg": _safe_float(d.get("kierunek_wiatru")),
        "current_pressure_hpa": _safe_float(d.get("cisnienie")),
        "current_humidity_pct": _safe_float(d.get("wilgotnosc_wzgledna")),
        "current_precip_10min_mm": _safe_float(d.get("suma_opadu")),
        "current_visibility_km": _safe_float(d.get("widocznosc")),
        "note": "Real-time synoptic observation from nearest station",
    }


METEOPL_API_KEY = os.environ.get("METEOPL_API_KEY", "")

async def fetch_meteopl(session: aiohttp.ClientSession, lat: float, lon: float, day_offset: int = 0) -> dict:
    """Meteo.pl (ICM Warsaw University) — high-res Polish NWP model. Requires METEOPL_API_KEY."""
    if not METEOPL_API_KEY:
        return {"source": "Meteo.pl (ICM Warsaw)", "error": "No METEOPL_API_KEY set. Get a key at api.meteo.pl"}

    # TODO: update endpoint path and auth method once API docs are confirmed
    url = "https://api.meteo.pl/api/v1/forecast/point"
    params = {"lat": lat, "lon": lon, "apikey": METEOPL_API_KEY}

    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                data = await r.json()
                return {
                    "source": "Meteo.pl (ICM Warsaw)",
                    "url": "https://meteo.pl",
                    "data": data,
                    "note": "ICM UM model — 1.5 km resolution over Poland",
                }
            return {"source": "Meteo.pl (ICM Warsaw)", "error": f"API error {r.status}"}
    except Exception as e:
        return {"source": "Meteo.pl (ICM Warsaw)", "error": str(e)}


async def fetch_openweathermap(session: aiohttp.ClientSession, lat: float, lon: float, day_offset: int = 0) -> dict:
    """OpenWeatherMap — requires free API key (OWM_API_KEY)."""
    if not OWM_API_KEY:
        return {"source": "OpenWeatherMap", "error": "No OWM_API_KEY set. Get a free key at openweathermap.org"}

    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {"lat": lat, "lon": lon, "appid": OWM_API_KEY, "units": "metric", "cnt": 40}
    async with session.get(url, params=params) as r:
        data = await r.json()

    if data.get("cod") != "200":
        return {"source": "OpenWeatherMap", "error": data.get("message", "API error")}

    all_items = data.get("list", [])
    # OWM returns 3h slots starting from NOW (not midnight).
    # Filter by date string prefix so day_offset works correctly at any time of day.
    from datetime import date as _date, timedelta as _td
    target_day = (_date.today() + _td(days=day_offset)).isoformat()  # "YYYY-MM-DD"
    items = [i for i in all_items if i.get("dt_txt", "").startswith(target_day)]
    first = all_items[0] if all_items else {}

    if not items:
        return {"source": "OpenWeatherMap", "error": "No data for requested date"}

    temps   = [i["main"]["temp"] for i in items]
    winds   = [i["wind"]["speed"] * 3.6 for i in items]
    precips = [i.get("rain", {}).get("3h", 0) + i.get("snow", {}).get("3h", 0) for i in items]

    hourly_detail = [
        {
            "dt_txt": i.get("dt_txt", ""),   # "2026-04-18 10:00:00" UTC
            "temp_c": i["main"]["temp"],
            "precip_mm": i.get("rain", {}).get("3h", 0) + i.get("snow", {}).get("3h", 0),
            "precip_prob_pct": round(i.get("pop", 0) * 100),
            "wind_kmh": round(i["wind"]["speed"] * 3.6, 1),
        }
        for i in items
    ]

    result = {
        "source": "OpenWeatherMap",
        "url": "https://openweathermap.org",
        "hourly_detail": hourly_detail,
        "next24h": {
            "temp_min": round(min(temps), 1) if temps else None,
            "temp_max": round(max(temps), 1) if temps else None,
            "total_precip_mm": round(sum(precips), 1),
            "avg_wind_kmh": round(sum(winds) / len(winds), 1) if winds else None,
        }
    }

    if day_offset == 0:
        result["current_temp_c"] = first.get("main", {}).get("temp")
        result["current_feels_like_c"] = first.get("main", {}).get("feels_like")
        result["current_humidity_pct"] = first.get("main", {}).get("humidity")
        result["current_wind_kmh"] = round(first.get("wind", {}).get("speed", 0) * 3.6, 1)
        result["current_windgust_kmh"] = round(first.get("wind", {}).get("gust", 0) * 3.6, 1)
        result["current_winddir_deg"] = first.get("wind", {}).get("deg")
        result["current_desc"] = first.get("weather", [{}])[0].get("description", "")

    return result


async def fetch_tomorrow_io(session: aiohttp.ClientSession, lat: float, lon: float, day_offset: int = 0) -> dict:
    """Tomorrow.io — requires free API key (TOMORROW_API_KEY)."""
    if not TOMORROW_API_KEY:
        return {"source": "Tomorrow.io", "error": "No TOMORROW_API_KEY set. Get a free key at tomorrow.io"}

    url = "https://api.tomorrow.io/v4/timelines"
    params = {
        "location": f"{lat},{lon}",
        "fields": "temperature,windSpeed,windGust,windDirection,precipitationIntensity,precipitationProbability,humidity,weatherCode,visibility",
        "timesteps": "1h",
        "units": "metric",
        "apikey": TOMORROW_API_KEY,
    }
    async with session.get(url, params=params) as r:
        data = await r.json()

    intervals = data.get("data", {}).get("timelines", [{}])[0].get("intervals", [])
    if not intervals:
        return {"source": "Tomorrow.io", "error": "No data"}

    # Use all intervals for hourly_detail so _build_by_time can filter by exact date.
    # `target` (day_offset slice) is used only for daily summary stats.
    slot_start = day_offset * 24
    target = intervals[slot_start:slot_start + 24]
    first_all = intervals[0].get("values", {})

    if not target:
        return {"source": "Tomorrow.io", "error": "No data for requested date"}

    temps = [i["values"]["temperature"] for i in target]
    winds = [i["values"]["windSpeed"] * 3.6 for i in target]
    gusts = [i["values"]["windGust"] * 3.6 for i in target]
    precip = [i["values"]["precipitationIntensity"] for i in target]

    hourly_detail = [
        {
            "start_time_utc": i.get("startTime", ""),
            "temp_c": i["values"].get("temperature"),
            "precip_mm": i["values"].get("precipitationIntensity"),
            "precip_prob_pct": i["values"].get("precipitationProbability"),
            "wind_kmh": round(i["values"].get("windSpeed", 0) * 3.6, 1),
        }
        for i in intervals  # full range; builder filters by target_date
    ]

    result = {
        "source": "Tomorrow.io",
        "url": "https://tomorrow.io",
        "hourly_detail": hourly_detail,
        "next24h": {
            "temp_min": round(min(temps), 1) if temps else None,
            "temp_max": round(max(temps), 1) if temps else None,
            "total_precip_mm": round(sum(precip), 1) if precip else None,
            "avg_wind_kmh": round(sum(winds) / len(winds), 1) if winds else None,
            "max_gust_kmh": round(max(gusts), 1) if gusts else None,
        }
    }

    if day_offset == 0:
        result["current_temp_c"] = first_all.get("temperature")
        result["current_humidity_pct"] = first_all.get("humidity")
        result["current_wind_kmh"] = round(first_all.get("windSpeed", 0) * 3.6, 1)
        result["current_windgust_kmh"] = round(first_all.get("windGust", 0) * 3.6, 1)
        result["current_winddir_deg"] = first_all.get("windDirection")
        result["current_visibility_km"] = first_all.get("visibility")

    return result


async def fetch_yrno(session: aiohttp.ClientSession, lat: float, lon: float, day_offset: int = 0) -> dict:
    """yr.no (Norwegian Met Institute) — free, no key, hourly ECMWF-based forecast."""
    url = "https://api.met.no/weatherapi/locationforecast/2.0/complete"
    params = {"lat": round(lat, 4), "lon": round(lon, 4)}
    headers = {"User-Agent": "CyclingWeatherCLI/1.0 github.com/cycling-weather"}
    async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
        if r.status != 200:
            return {"source": "yr.no", "error": f"API error {r.status}"}
        data = await r.json()

    timeseries = data.get("properties", {}).get("timeseries", [])
    if not timeseries:
        return {"source": "yr.no", "error": "No data"}

    # `target` (day_offset slice) is used only for daily summary stats.
    # hourly_detail uses the full timeseries; _build_by_time filters by exact local date.
    slot_start = day_offset * 24
    target = timeseries[slot_start:slot_start + 24]

    def _parse_yrno_entry(entry):
        instant = entry.get("data", {}).get("instant", {}).get("details", {})
        next1h  = entry.get("data", {}).get("next_1_hours", {})
        next6h  = entry.get("data", {}).get("next_6_hours", {})
        n1_det  = next1h.get("details", {})
        n6_det  = next6h.get("details", {})
        symbol  = next1h.get("summary", {}).get("symbol_code", "") or next6h.get("summary", {}).get("symbol_code", "")
        wind    = instant.get("wind_speed")
        gust    = instant.get("wind_speed_of_gust")
        prob    = n1_det.get("probability_of_precipitation") or n6_det.get("probability_of_precipitation")
        return {
            "start_time_utc": entry.get("time", ""),
            "temp_c":         instant.get("air_temperature"),
            "precip_mm":      n1_det.get("precipitation_amount"),
            "precip_prob_pct": prob,
            "wind_kmh":       round(wind * 3.6, 1) if wind is not None else None,
            "gust_kmh":       round(gust * 3.6, 1) if gust is not None else None,
            "wind_dir":       instant.get("wind_from_direction"),
            "symbol_code":    symbol,
        }

    # Stats from target slice; hourly_detail from all timeseries for date-accurate table lookups
    target_parsed = [_parse_yrno_entry(e) for e in target]
    hourly_detail  = [_parse_yrno_entry(e) for e in timeseries]

    temps  = [h["temp_c"]    for h in target_parsed if h["temp_c"]    is not None]
    winds  = [h["wind_kmh"]  for h in target_parsed if h["wind_kmh"]  is not None]
    gusts  = [h["gust_kmh"]  for h in target_parsed if h["gust_kmh"]  is not None]
    precip = [h["precip_mm"] for h in target_parsed if h["precip_mm"] is not None]

    first = target_parsed[0] if target_parsed else {}
    result = {
        "source": "yr.no",
        "url": "https://yr.no",
        "hourly_detail": hourly_detail,
        "next24h": {
            "temp_min": round(min(temps), 1) if temps else None,
            "temp_max": round(max(temps), 1) if temps else None,
            "total_precip_mm": round(sum(precip), 1) if precip else None,
            "avg_wind_kmh": round(sum(winds) / len(winds), 1) if winds else None,
            "max_gust_kmh": round(max(gusts), 1) if gusts else None,
        },
    }

    if day_offset == 0:
        result["current_temp_c"] = first.get("temp_c")
        result["current_wind_kmh"] = first.get("wind_kmh")
        result["current_windgust_kmh"] = first.get("gust_kmh")
        result["current_winddir_deg"] = first.get("wind_dir")

    return result


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _safe_float(val, multiply=1.0):
    try:
        return round(float(val) * multiply, 1)
    except (TypeError, ValueError):
        return None


def wind_direction(deg):
    if deg is None:
        return "?"
    dirs = ["N","NE","E","SE","S","SW","W","NW"]
    return dirs[round(deg / 45) % 8]


def wmo_code_to_desc(code):
    codes = {
        0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
        45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
        61: "Light rain", 63: "Rain", 65: "Heavy rain",
        71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
        80: "Light showers", 81: "Showers", 82: "Heavy showers",
        85: "Snow showers", 86: "Heavy snow showers",
        95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Heavy thunderstorm w/ hail",
    }
    return codes.get(code, f"Code {code}")


# ══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ══════════════════════════════════════════════════════════════════════════════

def print_warnings(warnings: list[dict]):
    if not warnings:
        return
    level_color = {1: YELLOW, 2: RED, "1": YELLOW, "2": RED}
    level_emoji = {1: "⚠️ ", 2: "🚨", "1": "⚠️ ", "2": "🚨"}
    print(f"  {BOLD}{RED}━━ IMGW WEATHER WARNINGS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    for w in warnings:
        # Field names vary — try known variants
        name    = w.get("nazwa_zagrozenia") or w.get("type") or w.get("name") or "Warning"
        level   = w.get("poziom_zagrozenia") or w.get("level") or 1
        region  = w.get("oddzial_IMGW") or w.get("region") or ""
        from_dt = w.get("od_dnia") or w.get("valid_from") or ""
        to_dt   = w.get("do_dnia") or w.get("valid_to") or ""
        msg     = w.get("komunikat") or w.get("description") or ""
        color   = level_color.get(level, YELLOW)
        emoji   = level_emoji.get(level, "⚠️ ")
        print(f"  {color}{emoji} Level {level} — {name}{RESET}", end="")
        if region:
            print(f"  {DIM}({region}){RESET}", end="")
        print()
        if from_dt or to_dt:
            print(f"    {DIM}Valid: {from_dt} → {to_dt}{RESET}")
        if msg:
            print(f"    {DIM}{msg}{RESET}")
    print()


def print_header(location_name: str, target_date: date):
    width = 70
    today = date.today()
    if target_date == today:
        date_str = f"Today — {target_date.strftime('%A, %d %B %Y')}"
    elif target_date == today + timedelta(days=1):
        date_str = f"Tomorrow — {target_date.strftime('%A, %d %B %Y')}"
    else:
        date_str = target_date.strftime("%A, %d %B %Y")
    print()
    print(f"{CYAN}{'━' * width}{RESET}")
    print(f"{CYAN}{BOLD}  🚴 CYCLING WEATHER REPORT{RESET}")
    print(f"{CYAN}  📍 {location_name[:60]}{RESET}")
    print(f"{CYAN}  📅 {date_str}{RESET}")
    if target_date == today:
        print(f"{CYAN}  🕐 {datetime.now().strftime('%H:%M')}{RESET}")
    print(f"{CYAN}{'━' * width}{RESET}")
    print()


def print_source_card(data: dict, day_label: str = "24h"):
    if "error" in data:
        print(f"  {YELLOW}⚠  {data['source']}: {data['error']}{RESET}")
        return

    source = data.get("source", "Unknown")
    print(f"  {BOLD}{WHITE}{source}{RESET}", end="")
    url = data.get("url", "")
    if url:
        print(f"  {GRAY}({url}){RESET}", end="")
    if data.get("station_name"):
        print(f"  {DIM}[station: {data['station_name']}]{RESET}", end="")
    print()

    # Current conditions (today only)
    temp = data.get("current_temp_c")
    feels = data.get("current_feels_like_c")
    wind = data.get("current_wind_kmh")
    gust = data.get("current_windgust_kmh") or data.get("current_wind_kmh")
    wdir = data.get("current_winddir_deg")
    hum = data.get("current_humidity_pct")
    desc = data.get("current_desc") or wmo_code_to_desc(data.get("weathercode"))
    pressure = data.get("current_pressure_hpa")
    vis = data.get("current_visibility_km")

    parts = []
    if temp is not None:
        t_str = f"{GREEN}{temp}°C{RESET}"
        if feels is not None and feels != temp:
            t_str += f" {DIM}(feels {feels}°C){RESET}"
        parts.append(t_str)
    if wind is not None:
        w_str = f"💨 {wind} km/h"
        if gust and gust != wind:
            w_str += f" (gusts {gust})"
        if wdir is not None:
            w_str += f" {wind_direction(wdir)}"
        parts.append(w_str)
    if hum:
        parts.append(f"💧 {hum}%")
    if pressure:
        parts.append(f"🌡 {pressure} hPa")
    if vis:
        parts.append(f"👁 {vis} km")
    if desc and desc != "Code None":
        parts.append(f"☁ {desc}")

    if parts:
        print(f"    {DIM}Now:{RESET}  " + f"  {GRAY}|{RESET}  ".join(parts))

    # Target day
    n24 = data.get("next24h", {})
    if n24:
        parts24 = []
        if n24.get("temp_min") is not None and n24.get("temp_max") is not None:
            parts24.append(f"{BLUE}{n24['temp_min']}–{n24['temp_max']}°C{RESET}")
        if n24.get("total_precip_mm") is not None:
            color = RED if n24["total_precip_mm"] > 5 else YELLOW if n24["total_precip_mm"] > 0.5 else GREEN
            parts24.append(f"{color}☔ {n24['total_precip_mm']} mm{RESET}")
        if n24.get("avg_wind_kmh"):
            parts24.append(f"💨 avg {n24['avg_wind_kmh']} km/h")
        if n24.get("max_gust_kmh"):
            parts24.append(f"max gust {n24['max_gust_kmh']} km/h")
        if n24.get("avg_humidity_pct"):
            parts24.append(f"💧 {n24['avg_humidity_pct']}%")
        if n24.get("sunrise"):
            parts24.append(f"🌅 {n24['sunrise']} / 🌇 {n24.get('sunset','')}")
        if parts24:
            print(f"    {DIM}{day_label}:{RESET}  " + f"  {GRAY}|{RESET}  ".join(parts24))

    # Tomorrow (only shown when viewing today)
    tom = data.get("tomorrow", {})
    if tom and any(v is not None for v in tom.values()):
        parts_tom = []
        if tom.get("temp_min") is not None and tom.get("temp_max") is not None:
            parts_tom.append(f"{BLUE}{tom['temp_min']}–{tom['temp_max']}°C{RESET}")
        if tom.get("precip_mm") is not None:
            color = RED if tom["precip_mm"] > 5 else YELLOW if tom["precip_mm"] > 0.5 else GREEN
            parts_tom.append(f"{color}☔ {tom['precip_mm']} mm{RESET}")
        if tom.get("max_wind_kmh") or tom.get("avg_wind_kmh"):
            w = tom.get("max_wind_kmh") or tom.get("avg_wind_kmh")
            parts_tom.append(f"💨 {w} km/h")
        if parts_tom:
            print(f"    {DIM}Tomorrow:{RESET}  " + f"  {GRAY}|{RESET}  ".join(parts_tom))

    if data.get("note"):
        print(f"    {DIM}ℹ  {data['note']}{RESET}")
    print()


# ══════════════════════════════════════════════════════════════════════════════
# HOURLY TABLE
# ══════════════════════════════════════════════════════════════════════════════

def _wmo_emoji(code):
    if code is None:
        return "   "
    if code <= 1:   return "☀️ "
    if code <= 2:   return "⛅ "
    if code <= 3:   return "☁️ "
    if code <= 48:  return "🌫 "
    if code <= 67:  return "🌧 "
    if code <= 77:  return "❄️ "
    if code <= 82:  return "🌦 "
    if code <= 86:  return "🌨 "
    return "⛈ "


def _prob_str(prob):
    """Rain probability — right-aligned 3 chars, no % sign."""
    if prob is None:        return f"{'─':>3}"
    if prob >= 60:          return f"{RED}{int(prob):>3}{RESET}"
    if prob >= 30:          return f"{YELLOW}{int(prob):>3}{RESET}"
    return f"{DIM}{int(prob):>3}{RESET}"


def _mm_str(mm):
    """Precipitation — right-aligned 4 chars, no mm unit."""
    if mm is None:          return f"{'─':>4}"
    if mm >= 2:             return f"{RED}{mm:>4.1f}{RESET}"
    if mm >= 0.1:           return f"{YELLOW}{mm:>4.1f}{RESET}"
    return f"{DIM}{mm:>4.1f}{RESET}"


def print_hourly_table(om_hours: list[dict], tio_by_time: dict = None,
                       yrno_by_time: dict = None, owm_by_time: dict = None):
    if not om_hours:
        return

    has_tio  = bool(tio_by_time)
    has_yrno = bool(yrno_by_time)
    has_owm  = bool(owm_by_time)

    print(f"{BOLD}{WHITE}  ━━ HOURLY FORECAST ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    # Source group labels — ─ used as fill char, centered over each group's columns
    # OM group: T°(4) + 2 + %(3) + 2 + mm(4) + 2 + kmh(4) + 2 + sky(2) = 25 visible chars
    # Extra groups: %(3) + 2 + mm(4) = 9 visible chars each, preceded by " │ "
    hdr = f"  {DIM}{'':5}  {'Open-Meteo':─^25}"
    if has_tio:  hdr += f" │ {'TIO':─^9}"
    if has_yrno: hdr += f" │ {'yr.no':─^9}"
    if has_owm:  hdr += f" │ {'OWM 3h':─^9}"
    print(hdr + RESET)

    # Column unit headers
    col = f"  {DIM}{'Time':<5}  {'T°':>4}  {'%':>3}  {'mm':>4}  {'kmh':>4}  {'':2}"
    if has_tio:  col += f" │ {'%':>3}  {'mm':>4}"
    if has_yrno: col += f" │ {'%':>3}  {'mm':>4}"
    if has_owm:  col += f" │ {'%':>3}  {'mm':>4}"
    print(col + RESET)

    # Separator: ─ throughout, ┼ at each group boundary
    sep = f"  {'─'*5}──{'─'*4}──{'─'*3}──{'─'*4}──{'─'*4}──{'─'*2}"
    if has_tio:  sep += f"─┼─{'─'*3}──{'─'*4}"
    if has_yrno: sep += f"─┼─{'─'*3}──{'─'*4}"
    if has_owm:  sep += f"─┼─{'─'*3}──{'─'*4}"
    print(f"{GRAY}{sep}{RESET}")

    for h in om_hours:
        time_str = h.get("time", "")
        temp = h.get("temp_c")
        prob = h.get("precip_prob_pct")
        mm   = h.get("precip_mm")
        wind = h.get("wind_kmh")
        code = h.get("weathercode")

        if temp is None:  temp_str = f"{'?':>3}°"
        elif temp < 5:    temp_str = f"{CYAN}{temp:>3.0f}°{RESET}"
        elif temp < 15:   temp_str = f"{BLUE}{temp:>3.0f}°{RESET}"
        else:             temp_str = f"{GREEN}{temp:>3.0f}°{RESET}"

        wind_str = f"{wind:>4.0f}" if wind is not None else f"{'?':>4}"
        sky = _wmo_emoji(code)

        row = f"  {BOLD}{time_str:<5}{RESET}  {temp_str}  {_prob_str(prob)}  {_mm_str(mm)}  {wind_str}  {sky}"

        if has_tio:
            t = tio_by_time.get(time_str, {})
            row += f" │ {_prob_str(t.get('precip_prob_pct') if t else None)}  {_mm_str(t.get('precip_mm') if t else None)}"

        if has_yrno:
            y = yrno_by_time.get(time_str, {})
            row += f" │ {_prob_str(y.get('precip_prob_pct') if y else None)}  {_mm_str(y.get('precip_mm') if y else None)}"

        if has_owm:
            o = owm_by_time.get(time_str, {})
            row += f" │ {_prob_str(o.get('precip_prob_pct') if o else None)}  {_mm_str(o.get('precip_mm') if o else None)}"

        print(row)

    print()


# ══════════════════════════════════════════════════════════════════════════════
# AI SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

async def ai_summarize(session: aiohttp.ClientSession, all_data: list[dict], location: str,
                       days: int = 1, target_date: date = None, warnings: list[dict] = None,
                       hourly_table: str = "") -> str:
    if not ANTHROPIC_API_KEY:
        return "⚠  Set ANTHROPIC_API_KEY to enable AI summary."

    clean = []
    for d in all_data:
        if "error" not in d:
            clean.append({k: v for k, v in d.items() if v is not None and k != "hourly_detail"})

    system = """You are a cycling-specific weather assistant. Analyze weather data from multiple sources and give a practical, concise summary for a gravel/road cyclist.

You are given two datasets:
1. Per-source daily summaries (temperature ranges, totals, averages)
2. An hourly cross-source table showing rain probability and precipitation per hour from up to 4 sources

Use the hourly table to:
- Identify the exact hour rain is likely to start (look for when 2+ sources show rain probability rising above 30%)
- Flag hours where sources strongly disagree (one shows 0% another shows 60%+) — mention the uncertainty
- Find dry windows within the day

Focus on (in this order):
1. Temperature and thermal comfort (min/max, layering advice)
2. Precipitation: risk, total mm, and exact hour rain is expected based on cross-source agreement
3. Wind: direction, speed, gust impact on cycling effort
4. Best time window to ride (specific hours)
5. Any specific hazards

Format your response for a plain terminal — NO markdown, no **bold**, no asterisks.
Use emojis for visual structure. Format:

🟢 / 🟡 / 🟠 / 🔴  <one-line verdict>

  🌡  <temperature & layering line>
  ☔  <precipitation line — exact start hour based on source agreement, or note if sources disagree>
  💨  <wind & gust line>
  🕐  <best ride window with specific hours>
  ⚠️  <hazards or source disagreements worth noting, if any>

➡  Recommended action: <one sentence>

Be direct and practical. Assume the cyclist has a gravel bike and may be doing 2-5 hours outdoors."""

    today = date.today()
    if target_date and target_date != today:
        if target_date == today + timedelta(days=1):
            period_desc = "tomorrow"
        else:
            period_desc = target_date.strftime("%A, %d %B %Y")
    else:
        period_desc = f"next {days} day(s)"

    warnings_section = ""
    if warnings:
        warnings_section = f"\nActive IMGW weather warnings:\n{json.dumps(warnings, indent=2, default=str)}\n"

    hourly_section = f"\nHourly cross-source table (Rain=%, Prcp=mm):\n{hourly_table}\n" if hourly_table else ""

    prompt = f"""Location: {location}
Forecast period: {period_desc}
{warnings_section}{hourly_section}
Per-source daily summaries ({len(clean)} sources):
{json.dumps(clean, indent=2, default=str)}

Give a cycling-focused weather summary."""

    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 800,
        "system": system,
        "messages": [{"role": "user", "content": prompt}]
    }
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    async with session.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers) as r:
        resp = await r.json()

    return resp.get("content", [{}])[0].get("text", "AI summary unavailable.")


def _strip_markdown(text: str) -> str:
    """Convert markdown bold/italic to ANSI, strip remaining markers."""
    import re
    # **bold** → ANSI bold
    text = re.sub(r'\*\*(.+?)\*\*', lambda m: f"{BOLD}{m.group(1)}{RESET}", text)
    # *italic* or _italic_ → dim
    text = re.sub(r'\*(.+?)\*', lambda m: f"{DIM}{m.group(1)}{RESET}", text)
    text = re.sub(r'_(.+?)_', lambda m: f"{DIM}{m.group(1)}{RESET}", text)
    return text


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def run(location: str, days: int = 1, no_ai: bool = False, target_date: date = None, day_offset: int = 0):
    today = date.today()
    if target_date is None:
        target_date = today

    if target_date == today:
        day_label = "24h"
    elif target_date == today + timedelta(days=1):
        day_label = "Tomorrow"
    else:
        day_label = target_date.strftime("%a %d %b")

    async with aiohttp.ClientSession() as session:
        print(f"{DIM}📡 Locating {location!r}...{RESET}", end="\r", flush=True)
        try:
            lat, lon, location_name = await geocode(session, location)
        except ValueError as e:
            print(f"{RED}✗ {e}{RESET}")
            sys.exit(1)
        # Clear the "Locating..." spinner line
        print(" " * 60, end="\r")

        print_header(location_name, target_date)

        print(f"{DIM}🌐 Fetching from 7 weather sources...{RESET}")
        tasks = [
            fetch_open_meteo(session, lat, lon, day_offset),
            fetch_wttr(session, lat, lon, day_offset),
            fetch_imgw(session, lat, lon, day_offset),
            fetch_meteopl(session, lat, lon, day_offset),
            fetch_openweathermap(session, lat, lon, day_offset),
            fetch_tomorrow_io(session, lat, lon, day_offset),
            fetch_yrno(session, lat, lon, day_offset),
            fetch_imgw_warnings(session),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        warnings = results[-1] if isinstance(results[-1], list) else []
        results = results[:-1]
        all_data = []
        for r in results:
            if isinstance(r, Exception):
                all_data.append({"source": "Unknown", "error": str(r)})
            else:
                all_data.append(r)

        # Build date-accurate hourly lookups before printing cards so summary stats can be patched.
        om_result   = next((d for d in all_data if d.get("source") == "Open-Meteo"),       None)
        tio_result  = next((d for d in all_data if d.get("source") == "Tomorrow.io"   and "hourly_detail" in d), None)
        yrno_result = next((d for d in all_data if d.get("source") == "yr.no"          and "hourly_detail" in d), None)
        owm_result  = next((d for d in all_data if d.get("source") == "OpenWeatherMap" and "hourly_detail" in d), None)

        utc_offset = timedelta(seconds=(om_result.get("utc_offset_seconds", 0) if om_result else 0))

        def _build_by_time(result, time_key="start_time_utc"):
            """Build {HH:MM → entry} for entries whose local date == target_date."""
            by_time = {}
            for h in result.get("hourly_detail", []):
                raw = h.get(time_key, "")
                if raw:
                    try:
                        utc_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        local_dt = utc_dt + utc_offset
                        if local_dt.date() == target_date:
                            by_time[local_dt.strftime("%H:%M")] = h
                    except ValueError:
                        pass
            return by_time

        def _build_owm_by_time(result):
            """Build {HH:MM → entry} for OWM 3h slots whose local date == target_date."""
            by_time = {}
            for h in result.get("hourly_detail", []):
                raw = h.get("dt_txt", "")   # "2026-04-18 10:00:00" UTC
                if raw:
                    try:
                        utc_dt = datetime.fromisoformat(raw.replace(" ", "T") + "+00:00")
                        local_dt = utc_dt + utc_offset
                        if local_dt.date() == target_date:
                            by_time[local_dt.strftime("%H:%M")] = h
                    except ValueError:
                        pass
            return by_time

        tio_by_time  = _build_by_time(tio_result)     if tio_result  else {}
        yrno_by_time = _build_by_time(yrno_result)    if yrno_result else {}
        owm_by_time  = _build_owm_by_time(owm_result) if owm_result  else {}

        # Patch TIO and yr.no summary stats using only the correctly date-filtered hours.
        # Fetchers slice by index (day_offset*N) which is wrong when APIs start from current hour.
        def _patch_next24h(result, by_time):
            if not result or not by_time:
                return
            entries = list(by_time.values())
            temps  = [e["temp_c"]       for e in entries if e.get("temp_c")       is not None]
            precip = [e["precip_mm"]    for e in entries if e.get("precip_mm")    is not None]
            winds  = [e["wind_kmh"]     for e in entries if e.get("wind_kmh")     is not None]
            gusts  = [e["gust_kmh"]     for e in entries if e.get("gust_kmh")     is not None]
            result["next24h"] = {
                "temp_min":        round(min(temps), 1)             if temps  else None,
                "temp_max":        round(max(temps), 1)             if temps  else None,
                "total_precip_mm": round(sum(precip), 1)            if precip else None,
                "avg_wind_kmh":    round(sum(winds) / len(winds), 1) if winds  else None,
                "max_gust_kmh":    round(max(gusts), 1)             if gusts  else None,
            }

        _patch_next24h(tio_result,  tio_by_time)
        _patch_next24h(yrno_result, yrno_by_time)

        print_warnings(warnings)
        print(f"\n{BOLD}{WHITE}  ━━ RAW FORECASTS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}\n")
        for d in all_data:
            print_source_card(d, day_label)

        # Hourly table — Open-Meteo base, all others as cross-references

        hourly_table = ""
        if om_result and om_result.get("hourly_detail"):
            print_hourly_table(
                om_result["hourly_detail"],
                tio_by_time  or None,
                yrno_by_time or None,
                owm_by_time  or None,
            )

            # Build compact text table for AI prompt
            rows = ["Hour   Temp  OM-Rain  OM-Prcp  TIO-Rain  TIO-Prcp  YR-Rain  OWM-Rain  OWM-Prcp"]
            for h in om_result["hourly_detail"]:
                t = h.get("time", "")
                temp = f"{h['temp_c']:.0f}°" if h.get("temp_c") is not None else "?"
                om_r = f"{int(h['precip_prob_pct'])}%" if h.get("precip_prob_pct") is not None else "─"
                om_p = f"{h['precip_mm']:.1f}" if h.get("precip_mm") is not None else "─"
                tio  = tio_by_time.get(t, {})
                tio_r = f"{int(tio['precip_prob_pct'])}%" if tio.get("precip_prob_pct") is not None else "─"
                tio_p = f"{tio['precip_mm']:.1f}" if tio.get("precip_mm") is not None else "─"
                yr   = yrno_by_time.get(t, {})
                yr_r  = f"{int(yr['precip_prob_pct'])}%" if yr.get("precip_prob_pct") is not None else "─"
                owm  = owm_by_time.get(t, {})
                owm_r = f"{int(owm['precip_prob_pct'])}%" if owm.get("precip_prob_pct") is not None else "─"
                owm_p = f"{owm['precip_mm']:.1f}" if owm.get("precip_mm") is not None else "─"
                rows.append(f"{t}   {temp:>4}  {om_r:>7}  {om_p:>7}  {tio_r:>8}  {tio_p:>8}  {yr_r:>7}  {owm_r:>8}  {owm_p:>8}")
            hourly_table = "\n".join(rows)

        if not no_ai:
            print(f"{BOLD}{WHITE}  ━━ AI CYCLING SUMMARY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}\n")
            print(f"{DIM}  🤖 Asking Claude...{RESET}")
            summary = await ai_summarize(session, all_data, location_name, days, target_date, warnings, hourly_table)
            print()
            for line in summary.split("\n"):
                stripped = line.strip()
                indent = "  " if stripped else ""
                print(f"{indent}{_strip_markdown(stripped)}")
            print()

        print(f"{CYAN}{'━' * 70}{RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        description="🚴 Cycling Weather CLI — multi-source forecast with AI summary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python weather_cli.py "Łódź, Poland"
  python weather_cli.py "Gdynia" --date tomorrow
  python weather_cli.py "Kraków" --date 2026-04-20
  python weather_cli.py "51.75,19.46"        # lat,lon directly
  python weather_cli.py "Kraków" --days 2 --no-ai

Environment variables:
  ANTHROPIC_API_KEY   — for AI summary (required for AI)
  OWM_API_KEY         — OpenWeatherMap free key (optional)
  TOMORROW_API_KEY    — Tomorrow.io free key (optional)
        """
    )
    parser.add_argument("location", help="City name or 'lat,lon' coordinates")
    parser.add_argument("--days", type=int, default=1, choices=[1, 2, 3], help="Forecast days (1-3, default: 1)")
    parser.add_argument("--date", default=None, metavar="DATE",
                        help="Target date: 'today', 'tomorrow', or YYYY-MM-DD (default: today)")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI summary")
    args = parser.parse_args()

    today = date.today()
    if args.date is None or args.date.lower() == "today":
        target_date = today
    elif args.date.lower() == "tomorrow":
        target_date = today + timedelta(days=1)
    else:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"{RED}Invalid date '{args.date}'. Use 'today', 'tomorrow', or YYYY-MM-DD.{RESET}")
            sys.exit(1)

    day_offset = (target_date - today).days
    if day_offset < 0 or day_offset > 2:
        print(f"{RED}Date must be today, tomorrow, or up to 2 days ahead.{RESET}")
        sys.exit(1)

    try:
        asyncio.run(run(args.location, args.days, args.no_ai, target_date, day_offset))
    except KeyboardInterrupt:
        print(f"\n{DIM}Cancelled.{RESET}")


if __name__ == "__main__":
    main()
