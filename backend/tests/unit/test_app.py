import asyncio
import json
import uuid

import pytest
from deepseek_harness import Notification
from fastmcp import Client
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import telemetry
from app import browser_event, llm_config, parse_chat_request, resumed_prompt, sse_frame
from database import _title
from mcp_server import server


def test_llm_config_accepts_exactly_one_credential() -> None:
    assert llm_config({"OPENAI_TOKEN": "token"}) == ("openai-codex", "gpt-5.6-sol")
    assert llm_config({"OPENAI_API_KEY": "key", "OPENAI_MODEL": "gpt-5.4"}) == ("openai", "gpt-5.4")
    with pytest.raises(RuntimeError, match="exactly one"):
        llm_config({})
    with pytest.raises(RuntimeError, match="exactly one"):
        llm_config({"OPENAI_TOKEN": "token", "OPENAI_API_KEY": "key"})


def test_parse_chat_request_validates_input() -> None:
    session_id = uuid.uuid4()
    assert parse_chat_request(json.dumps({"message": " hi ", "session_id": str(session_id)}).encode()) == ("hi", session_id)
    with pytest.raises(ValueError, match="session_id is invalid"):
        parse_chat_request(b'{"message":"hi","session_id":"../escape"}')
    assert _title("a " * 40).endswith("...")
    prompt = resumed_prompt("What was it?", [{"role": "user", "content": "Remember ORCHID"}])
    assert "Remember ORCHID" in prompt and "What was it?" in prompt


def test_harness_events_become_browser_sse() -> None:
    call = Notification("session.event", {"event": {
        "type": "tool/call", "data": {"callId": "call-1", "name": "get_uk_weather", "arguments": '{"location":"Edinburgh"}'},
    }})
    result = Notification("session.event", {"event": {
        "type": "tool/result", "data": {"message": {
            "source": {"callId": "call-1"},
            "content": [{"type": "tool-result", "isError": False, "content": [{"type": "text", "text": "found"}]}],
        }},
    }})
    assistant = Notification("session.event", {"event": {
        "type": "assistant/message", "data": {"message": {"content": [{"type": "text", "text": "Done"}]}},
    }})

    assert browser_event(call) == ("tool_call", {"id": "call-1", "name": "get_uk_weather", "arguments": '{"location":"Edinburgh"}'})
    assert browser_event(result) == ("tool_result", {"id": "call-1", "result": "found", "is_error": False})
    assert browser_event(assistant) == ("assistant", {"text": "Done"})
    assert sse_frame("done", {"response": "Hi\nthere"}) == b'event: done\ndata: {"response":"Hi\\nthere"}\n\n'


def test_telemetry_keeps_descendant_agent_events() -> None:
    notifications = [
        Notification("session.event", {"sessionId": "root", "event": {"type": "turn/start"}}),
        Notification("subagent.started", {"parentSessionId": "root", "childSessionId": "child"}),
        Notification("session.event", {"sessionId": "child", "event": {"type": "tool/call"}}),
    ]
    events = telemetry._notification_events(notifications)
    assert [event["_agent_session_id"] for event in events] == ["root", "child"]


def test_harness_events_become_only_llm_and_tool_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    telemetry.TRACER = provider.get_tracer("test")
    events = [
        {"type": "request/header", "time": 1, "data": {"header": {"config": {"provider": "openai-codex", "model": "gpt-5.6-sol"}}}},
        {"type": "step/start", "time": 2, "data": {"turn": 1, "step": 1}},
        {"type": "assistant/message", "time": 3, "data": {"turn": 1, "step": 1, "message": {"content": [{"type": "tool-call"}], "source": {"provider": "openai-codex", "model": "gpt-5.6-sol"}}, "usage": {"inputTokens": 10, "outputTokens": 5}}},
        {"type": "tool/call", "time": 4, "data": {"callId": "call-1", "name": "get_uk_weather", "arguments": "{}"}},
        {"type": "tool/result", "time": 5, "data": {"message": {"source": {"callId": "call-1"}, "content": [{"type": "tool-result", "isError": False, "content": [{"type": "text", "text": "found"}]}]}}},
        {"type": "step/start", "time": 6, "data": {"turn": 1, "step": 2}},
        {"type": "assistant/message", "time": 7, "data": {"turn": 1, "step": 2, "message": {"content": [{"type": "text", "text": "done"}], "source": {"provider": "openai-codex", "model": "gpt-5.6-sol"}}, "usage": {"inputTokens": 12, "outputTokens": 2}}},
    ]

    telemetry._record_children(events, "question", "session-1", True)
    spans = exporter.get_finished_spans()
    assert [span.attributes["openinference.span.kind"] for span in spans] == ["LLM", "TOOL", "LLM"]
    assert spans[0].attributes["llm.token_count.total"] == 15
    assert spans[1].attributes["tool.name"] == "get_uk_weather"
    assert spans[1].attributes["agent.session.id"] == "root"
    assert all("http.route" not in span.attributes and "db.system" not in span.attributes for span in spans)


def test_mcp_server_exposes_only_specialist_tools() -> None:
    async def check() -> None:
        async with Client(server) as client:
            assert {tool.name for tool in await client.list_tools()} == {
                "get_uk_weather",
                "suggest_uk_activities",
            }
            weather = await client.call_tool("get_uk_weather", {"location": "Edinburgh", "month": "January"})
            assert weather.data["estimated_average_c"] == 4
            activities = await client.call_tool("suggest_uk_activities", {
                "city": "Edinburgh",
                "weather": "rainy",
                "month": "January",
            })
            assert activities.data["fictional"] is True
            assert len(activities.data["activities"]) == 3

    asyncio.run(check())
