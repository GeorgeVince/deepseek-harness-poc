#!/usr/bin/env python3
"""Small UK weather and fictional-activity tool server."""

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode
from urllib.request import urlopen

from fastmcp import FastMCP

server = FastMCP("UK Weather, Activities, and Sandbox")
SANDBOX_SOCKET = os.environ.get("SANDBOX_SOCKET", "/run/sandbox/runner.sock")
MAX_SANDBOX_RESPONSE_BYTES = 70_000

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
BASE_MONTHLY_C = (5, 6, 8, 10, 13, 16, 18, 18, 15, 11, 8, 6)
CITY_OFFSETS = {
    "London": 2,
    "Edinburgh": -1,
    "Manchester": 0,
    "Cardiff": 1,
    "Belfast": 0,
}
ACTIVITIES = {
    "London": {
        "wet": ["The Underground Art Hunt", "The Clockwork Tea Lab"],
        "fair": ["The Thames Story Trail", "The Hidden Gardens Quest"],
    },
    "Edinburgh": {
        "wet": ["The Old Town Puzzle Vault", "The Tartan Tales Workshop"],
        "fair": ["The Seven Hills Photo Quest", "The Secret Close Story Walk"],
    },
    "Manchester": {
        "wet": ["The Northern Quarter Sound Lab", "The Cottonopolis Maker Hall"],
        "fair": ["The Canal Code Trail", "The Bee Mural Safari"],
    },
    "Cardiff": {
        "wet": ["The Dragon Lore Studio", "The Arcades Mystery Hunt"],
        "fair": ["The Bay Wind Quest", "The Castle Walls Story Trail"],
    },
    "Belfast": {
        "wet": ["The Linen Legends Workshop", "The Shipyard Signal Room"],
        "fair": ["The Maritime Mural Trail", "The Cave Hill Story Quest"],
    },
}
SEASONAL_ACTIVITY = {
    "winter": "The Winter Lantern Club",
    "spring": "The Spring City Bloom Hunt",
    "summer": "The Long-Evening Street Picnic",
    "autumn": "The Autumn Legends Trail",
}


def _month(month: str | None) -> int:
    if month is None:
        return datetime.now(timezone.utc).month
    number = MONTHS.get(month.strip().lower())
    if number is None:
        raise ValueError("month must be a full English month name")
    return number


def _city(value: str) -> str:
    normalized = value.strip().casefold()
    for city in CITY_OFFSETS:
        if city.casefold() == normalized:
            return city
    raise ValueError(f"city must be one of: {', '.join(CITY_OFFSETS)}")


def _condition(code: int) -> str:
    if code == 0:
        return "clear"
    if code in {1, 2, 3, 45, 48}:
        return "cloudy"
    if code in {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}:
        return "rainy"
    if code in {71, 73, 75, 77, 85, 86}:
        return "snowy"
    if code in {95, 96, 99}:
        return "stormy"
    return "mixed"


def _get_json(url: str) -> dict:
    try:
        with urlopen(url, timeout=10) as response:  # noqa: S310 - fixed HTTPS APIs only
            data = json.load(response)
        if not isinstance(data, dict):
            raise ValueError("unexpected response")
        return data
    except Exception as error:
        raise RuntimeError("weather service is unavailable") from error


def _purpose(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("purpose must contain 1 to 200 characters")
    value = value.strip()
    if not value or len(value) > 200:
        raise ValueError("purpose must contain 1 to 200 characters")
    return value


def _workbooks(root: Path | None = None) -> dict[str, tuple[int, int]]:
    files = {}
    for path in (root or Path.cwd()).glob("*.xlsx"):
        if path.is_file() and not path.is_symlink():
            info = path.stat()
            files[path.name] = (info.st_size, info.st_mtime_ns)
    return files


def _workbook_artifacts(
    before: dict[str, tuple[int, int]], after: dict[str, tuple[int, int]]
) -> list[dict]:
    return [
        {"name": name, "size": info[0], "change": "created" if name not in before else "updated"}
        for name, info in sorted(after.items())
        if before.get(name) != info
    ]


def _run_sandboxed(kind: str, code: str, timeout_seconds: int) -> dict:
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 30:
        raise ValueError("timeout_seconds must be between 1 and 30")
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > 20_000:
        raise ValueError("code must contain 1 to 20000 bytes")
    request = json.dumps({"kind": kind, "code": code, "timeout": timeout_seconds}).encode() + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout_seconds + 5)
            client.connect(SANDBOX_SOCKET)
            client.sendall(request)
            response = client.makefile("rb").readline(MAX_SANDBOX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError) as error:
        raise RuntimeError("sandbox runner is unavailable") from error
    if len(response) > MAX_SANDBOX_RESPONSE_BYTES:
        raise RuntimeError("sandbox runner returned too much data")
    result = json.loads(response)
    if not isinstance(result, dict):
        raise RuntimeError("sandbox runner returned an invalid response")
    if result.get("error"):
        raise RuntimeError(str(result["error"]))
    return result


@server.tool
def get_uk_weather(location: str, month: str | None = None) -> dict:
    """Get the current temperature for a UK location, or a rough monthly estimate for a supported city."""
    if month is not None:
        city = _city(location)
        number = _month(month)
        return {
            "location": city,
            "month": month.strip().title(),
            "estimated_average_c": BASE_MONTHLY_C[number - 1] + CITY_OFFSETS[city],
            "kind": "rough_climate_estimate",
        }

    location = location.strip()
    if not location:
        raise ValueError("location is required")
    query = urlencode({"name": location, "count": 1, "language": "en", "format": "json", "countryCode": "GB"})
    places = _get_json(f"https://geocoding-api.open-meteo.com/v1/search?{query}").get("results")
    if not isinstance(places, list) or not places:
        raise ValueError("UK location was not found")
    place = places[0]
    if (
        not isinstance(place, dict)
        or place.get("country_code") != "GB"
        or not isinstance(place.get("name"), str)
        or not all(isinstance(place.get(key), (int, float)) for key in ("latitude", "longitude"))
    ):
        raise RuntimeError("weather service returned an invalid location")
    query = urlencode({
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,weather_code",
        "timezone": "Europe/London",
    })
    current = _get_json(f"https://api.open-meteo.com/v1/forecast?{query}").get("current")
    if not isinstance(current, dict) or not isinstance(current.get("temperature_2m"), (int, float)):
        raise RuntimeError("weather service returned no current conditions")
    try:
        condition = _condition(int(current["weather_code"]))
        observed_at = str(current["time"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("weather service returned invalid current conditions") from error
    return {
        "location": f"{place['name']}, {place.get('admin1') or 'UK'}",
        "temperature_c": current["temperature_2m"],
        "condition": condition,
        "observed_at": observed_at,
        "source": "Open-Meteo",
    }


def _run_with_artifacts(kind: str, purpose: str, code: str, timeout_seconds: int) -> dict:
    _purpose(purpose)
    before = _workbooks()
    result = _run_sandboxed(kind, code, timeout_seconds)
    if result.get("timed_out"):
        raise RuntimeError("sandbox execution timed out")
    if result.get("exit_code") != 0:
        raise RuntimeError(result.get("stderr") or "sandbox execution failed")
    if artifacts := _workbook_artifacts(before, _workbooks()):
        result["artifacts"] = artifacts
    return result


@server.tool
def run_bash(purpose: str, command: str, timeout_seconds: int = 30) -> dict:
    """Run Bash in the isolated workspace. Purpose is a brief user-facing explanation of what this call does and why; do not include code or private reasoning."""
    return _run_with_artifacts("bash", purpose, command, timeout_seconds)


@server.tool
def run_python(purpose: str, code: str, timeout_seconds: int = 30) -> dict:
    """Run Python in the isolated workspace. Purpose is a brief user-facing explanation of what this call does and why; do not include code or private reasoning. Formualizer 0.8.4 is installed for XLSX: use formualizer.load_workbook(path) or Workbook(), then write Workbook.to_xlsx_bytes() to a new /workspace file."""
    return _run_with_artifacts("python", purpose, code, timeout_seconds)


@server.tool
def suggest_uk_activities(
    city: Literal["London", "Edinburgh", "Manchester", "Cardiff", "Belfast"],
    weather: str,
    month: str | None = None,
) -> dict:
    """Return fictional city activities selected for the supplied weather and time of year."""
    weather = weather.strip()
    if not weather:
        raise ValueError("weather is required")
    number = _month(month)
    season = ("winter", "spring", "summer", "autumn")[((number % 12) // 3)]
    wet = any(word in weather.casefold() for word in ("rain", "snow", "storm", "wet"))
    names = [*ACTIVITIES[city]["wet" if wet else "fair"], SEASONAL_ACTIVITY[season]]
    return {
        "city": city,
        "weather": weather,
        "month": list(MONTHS)[number - 1].title(),
        "season": season,
        "fictional": True,
        "activities": [
            {"name": name, "why": f"Designed for {weather.lower()} {season} days in {city}."}
            for name in names
        ],
    }


if __name__ == "__main__":
    server.run()
