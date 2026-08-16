import asyncio
import io
import threading
import zipfile

import pytest
from deepseek_harness import Notification
from fastmcp import Client
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import ValidationError

import telemetry
from app import ChatPayload, app, browser_event, llm_config, process_upload, resumed_prompt, sse_frame, workbook_name, workspace_files
from database import _title
from mcp_server import _purpose, _workbook_artifacts, server


def test_llm_config_accepts_exactly_one_credential() -> None:
    assert llm_config({"OPENAI_TOKEN": "token"}) == ("openai-codex", "gpt-5.6-sol")
    assert llm_config({"OPENAI_API_KEY": "key", "OPENAI_MODEL": "gpt-5.4"}) == ("openai", "gpt-5.4")
    with pytest.raises(RuntimeError, match="exactly one"):
        llm_config({})
    with pytest.raises(RuntimeError, match="exactly one"):
        llm_config({"OPENAI_TOKEN": "token", "OPENAI_API_KEY": "key"})


def test_chat_payload_validates_input() -> None:
    assert ChatPayload(message=" hi ").message == "hi"
    with pytest.raises(ValidationError, match="message is required"):
        ChatPayload(message="   ")
    assert _title("a " * 40).endswith("...")
    prompt = resumed_prompt("What was it?", [{"role": "user", "content": "Remember ORCHID"}])
    assert "Remember ORCHID" in prompt and "What was it?" in prompt


def test_workbook_workspace_rejects_unsafe_names_and_symlinks(tmp_path) -> None:
    assert workbook_name("Forecast 2026.xlsx") == "Forecast 2026.xlsx"
    for name in ("../secret.xlsx", "report.xls", 'bad"name.xlsx'):
        with pytest.raises(ValueError, match="simple .xlsx"):
            workbook_name(name)
    workbook = tmp_path / "report.xlsx"
    workbook.write_bytes(b"PK\x03\x04data")
    (tmp_path / "alias.xlsx").symlink_to(workbook)
    assert [item["name"] for item in workspace_files(tmp_path)] == ["report.xlsx"]


def test_background_upload_validates_and_publishes_workbook(tmp_path) -> None:
    staging = tmp_path / ".upload.tmp"
    with zipfile.ZipFile(staging, "w") as workbook:
        workbook.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        workbook.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
    jobs = {"upload-1": {"id": "upload-1", "status": "processing"}}
    destination = tmp_path / "input.xlsx"

    process_upload("upload-1", staging, destination, jobs, threading.Lock())

    assert destination.is_file()
    assert jobs["upload-1"]["status"] == "complete"


def test_fastapi_upload_runs_background_validation(tmp_path) -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as workbook:
        workbook.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        workbook.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>')
    app.state.workspace = tmp_path
    app.state.upload_jobs = {}
    app.state.upload_lock = threading.Lock()

    async def check() -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/files",
                files={"file": ("browser.xlsx", payload.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
            assert response.status_code == 202
            upload_id = response.json()["upload"]["id"]
            status = await client.get(f"/api/uploads/{upload_id}")
            assert status.json()["upload"]["status"] == "complete"
            download = await client.get("/api/files/browser.xlsx")
            assert download.content == payload.getvalue()

    asyncio.run(check())


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
    sandbox_call = Notification("session.event", {"event": {
        "type": "tool/call", "data": {
            "callId": "call-2",
            "name": "mcp__python__run_python",
            "arguments": '{"purpose":"Inspecting the workbook headers","code":"print(123)"}',
        },
    }})
    assert browser_event(sandbox_call) == ("tool_call", {
        "id": "call-2",
        "name": "mcp__python__run_python",
        "arguments": '{"purpose":"Inspecting the workbook headers"}',
    })
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


def test_sandbox_purposes_and_workbook_artifacts() -> None:
    assert _purpose(" Inspecting workbook headers ") == "Inspecting workbook headers"
    with pytest.raises(ValueError, match="purpose"):
        _purpose(" ")
    before = {"source.xlsx": (100, 1)}
    after = {"source.xlsx": (100, 1), "result.xlsx": (250, 2)}
    assert _workbook_artifacts(before, after) == [
        {"name": "result.xlsx", "size": 250, "change": "created"}
    ]


def test_mcp_server_exposes_only_specialist_tools() -> None:
    async def check() -> None:
        async with Client(server) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
            assert set(tools) == {
                "get_uk_weather",
                "run_bash",
                "run_python",
                "suggest_uk_activities",
            }
            assert tools["run_bash"].inputSchema["required"] == ["purpose", "command"]
            assert tools["run_python"].inputSchema["required"] == ["purpose", "code"]
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
