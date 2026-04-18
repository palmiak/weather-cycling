# Cycling Weather CLI

A multi-source weather aggregator with AI summary, built for cyclists. Pulls data from up to 7 sources and uses Claude to produce a plain-language ride verdict.

## Weather sources

| Source | Free | Key required | Notes |
|--------|------|-------------|-------|
| Open-Meteo | Yes | No | Hourly data, wind gusts, WMO codes |
| wttr.in | Yes | No | Sunrise/sunset, feels-like |
| IMGW | Yes | No | Polish national synoptic station data |
| Meteo.pl (ICM) | Yes | `METEOPL_API_KEY` | 1.5 km resolution over Poland |
| OpenWeatherMap | Free tier | `OWM_API_KEY` | Good global coverage |
| Tomorrow.io | Free tier | `TOMORROW_API_KEY` | Good gust data |
| yr.no | Yes | No | Norwegian Met / ECMWF, precip probability + gusts |

The tool works with zero API keys (Open-Meteo + wttr.in + IMGW + yr.no). Adding OWM and Tomorrow.io keys unlocks 2 more sources.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and fill in your API keys
```

## Usage

```bash
# Basic — city name (today)
python weather_cli.py "Łódź"

# Tomorrow's forecast
python weather_cli.py "Łódź" --date tomorrow

# Specific date (up to 2 days ahead)
python weather_cli.py "Łódź" --date 2026-04-20

# Coordinates
python weather_cli.py "51.75,19.46"

# Multi-day forecast
python weather_cli.py "Gdynia" --days 2

# Skip AI summary
python weather_cli.py "Kraków" --no-ai
```

## Environment variables

Copy `.env.example` to `.env` and fill in the values you need:

| Variable | Required | Source |
|----------|----------|--------|
| `ANTHROPIC_API_KEY` | For AI summary | console.anthropic.com |
| `OWM_API_KEY` | Optional | openweathermap.org (free) |
| `TOMORROW_API_KEY` | Optional | tomorrow.io (free tier) |
| `METEOPL_API_KEY` | Optional | api.meteo.pl |

## AI summary

When `ANTHROPIC_API_KEY` is set, the CLI produces a structured ride assessment:

- Overall verdict (green / yellow / orange / red)
- Wind direction, speed, and gust impact
- Precipitation risk and road surface conditions
- Temperature and layering advice
- Best time window to ride
- Specific hazards (crosswinds, ice, storms)
