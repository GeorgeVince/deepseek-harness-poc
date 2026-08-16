#!/usr/bin/env python3
"""FastMCP gateway that discovers private Python tools and exposes search/call."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from fastmcp import Client, FastMCP

catalog = FastMCP("Example Python Tools")
gateway = FastMCP(
    "Python Tool Search",
    instructions="Search for a relevant tool before calling it. Pass search_id to call_tool and only call a returned tool.",
)
_searches: dict[str, set[str]] = {}


@catalog.tool
def calculate_total(prices: list[float], tax_rate: float = 0) -> dict[str, float]:
    """Calculate a cart, order, or invoice subtotal, tax, and total."""
    subtotal = sum(prices)
    tax = subtotal * tax_rate
    return {"subtotal": subtotal, "tax": tax, "total": subtotal + tax}


@catalog.tool
def convert_temperature(
    value: float,
    from_unit: Literal["celsius", "fahrenheit"],
    to_unit: Literal["celsius", "fahrenheit"],
) -> dict[str, float | str]:
    """Convert a temperature between Celsius and Fahrenheit."""
    if from_unit == to_unit:
        result = value
    elif from_unit == "celsius":
        result = value * 9 / 5 + 32
    else:
        result = (value - 32) * 5 / 9
    return {"value": result, "unit": to_unit}


@catalog.tool
def text_statistics(text: str) -> dict[str, int]:
    """Count words, Unicode characters, and lines in text."""
    return {
        "words": len(text.split()),
        "characters": len(text),
        "lines": len(text.splitlines()) or 1,
    }


@catalog.tool
def current_utc_time() -> dict[str, str]:
    """Get the current UTC date and time clock value."""
    return {"utc": datetime.now(timezone.utc).isoformat()}


@catalog.tool
def lookup_support_hours(day: str) -> dict[str, str]:
    """Look up customer support business opening hours for a weekday or weekend day."""
    normalized = day.strip().lower()
    hours = "09:00-17:00" if normalized in {"monday", "tuesday", "wednesday", "thursday", "friday"} else "closed"
    return {"day": normalized, "hours": hours}


def _tool_schema(tool: Any) -> dict[str, Any]:
    return getattr(tool, "inputSchema", getattr(tool, "input_schema", {}))


def _rank(query: str, tools: list[Any], limit: int) -> list[dict[str, Any]]:
    words = {word for word in re.findall(r"[a-z0-9]+", query.lower()) if len(word) > 2}
    ranked = []
    for tool in tools:
        haystack = f"{tool.name} {tool.description or ''}".lower()
        score = sum(2 if word in tool.name.lower() else 1 for word in words if word in haystack)
        if score:
            ranked.append((score, tool.name, tool))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"name": tool.name, "description": tool.description, "input_schema": _tool_schema(tool)}
        for _, _, tool in ranked[:limit]
    ]


@gateway.tool
async def search_tools(query: str, limit: int = 3) -> dict[str, Any]:
    """Search available tools by capability. Returns a search_id required by call_tool."""
    async with Client(catalog) as client:
        matches = _rank(query, await client.list_tools(), max(1, min(limit, 10)))
    search_id = secrets.token_urlsafe(12)
    _searches[search_id] = {match["name"] for match in matches}
    if len(_searches) > 100:
        _searches.pop(next(iter(_searches)))
    return {"search_id": search_id, "matches": matches}


@gateway.tool
async def call_tool(search_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one tool returned by search_tools, passing its search_id and schema-matching arguments."""
    allowed = _searches.pop(search_id, None)
    if allowed is None or name not in allowed:
        raise ValueError("Tool was not returned by that search; call search_tools first")
    async with Client(catalog) as client:
        result = await client.call_tool(name, arguments)
        return {"tool": name, "result": result.data}


if __name__ == "__main__":
    gateway.run()
