import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client

from app import parse_chat_request, read_pi_oauth
from mcp_server import gateway


def test_read_pi_oauth_accepts_only_current_tokens(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"openai-codex": {"type": "oauth", "access": "token", "expires": 1_000_000}}))
    assert read_pi_oauth(auth, 0) == "token"
    with pytest.raises(RuntimeError, match="expired or expiring"):
        read_pi_oauth(auth, 800_000)


def test_parse_chat_request_validates_input() -> None:
    assert parse_chat_request(b'{"message":" hi ","session_id":"abc-123"}') == ("hi", "abc-123")
    with pytest.raises(ValueError, match="session_id is invalid"):
        parse_chat_request(b'{"message":"hi","session_id":"../escape"}')


def test_mcp_gateway_exposes_only_search_and_call() -> None:
    async def check() -> None:
        async with Client(gateway) as client:
            assert {tool.name for tool in await client.list_tools()} == {"search_tools", "call_tool"}
            search = await client.call_tool("search_tools", {"query": "convert celsius to fahrenheit"})
            assert [match["name"] for match in search.data["matches"]] == ["convert_temperature"]
            call = await client.call_tool("call_tool", {
                "search_id": search.data["search_id"],
                "name": "convert_temperature",
                "arguments": {"value": 20, "from_unit": "celsius", "to_unit": "fahrenheit"},
            })
            assert call.data["result"]["value"] == 68

    asyncio.run(check())
