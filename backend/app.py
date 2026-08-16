#!/usr/bin/env python3
"""FastAPI browser chat backed by DeepSeek Harness and PostgreSQL."""

import argparse
import asyncio
import json
import os
import queue
import re
import stat
import sys
import threading
import uuid
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from deepseek_harness import DeepSeekHarness, Notification
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

import database
import telemetry

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
MAX_WORKBOOK_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_WORKBOOK_BYTES = 256 * 1024 * 1024
WORKBOOK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,120}\.xlsx", re.IGNORECASE)


class ChatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(max_length=20_000)

    @field_validator("message")
    @classmethod
    def message_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message is required")
        return value


def llm_config(env: Mapping[str, str]) -> tuple[str, str]:
    token = bool(env.get("OPENAI_TOKEN", "").strip())
    api_key = bool(env.get("OPENAI_API_KEY", "").strip())
    if token == api_key:
        raise RuntimeError("Set exactly one of OPENAI_TOKEN or OPENAI_API_KEY in .env")
    model = env.get("OPENAI_MODEL", "gpt-5.6-sol").strip()
    if not model:
        raise RuntimeError("OPENAI_MODEL cannot be empty")
    return ("openai-codex" if token else "openai"), model


def workbook_name(value: object) -> str:
    if not isinstance(value, str) or not WORKBOOK_NAME.fullmatch(value):
        raise ValueError("filename must be a simple .xlsx name")
    return value


def workspace_files(root: Path) -> list[dict[str, Any]]:
    files = []
    for path in root.iterdir():
        if path.suffix.lower() == ".xlsx" and path.is_file() and not path.is_symlink():
            info = path.stat()
            files.append({"name": path.name, "size": info.st_size, "modified_at": info.st_mtime})
    return sorted(files, key=lambda item: item["modified_at"], reverse=True)


def resumed_prompt(message: str, history: list[dict[str, Any]]) -> str:
    if not history:
        return message
    transcript = [{"role": item["role"], "content": item["content"]} for item in history]
    # ponytail: replay the full DB transcript on first use after restart; compact if histories become large.
    return f"Continue the prior conversation below.\n\n{json.dumps(transcript)}\n\nCurrent user message:\n{message}"


def _public_tool_arguments(name: str, arguments: object) -> str:
    if name.rsplit("__", 1)[-1] in {"run_bash", "run_python"}:
        return "{}"
    return arguments if isinstance(arguments, str) else json.dumps(arguments, separators=(",", ":"))


def browser_event(notification: Notification) -> tuple[str, dict[str, Any]] | None:
    """Turn useful Harness notifications into the browser's small event vocabulary."""
    if notification.method != "session.event":
        return None
    event = notification.payload.get("event")
    if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
        return None
    kind, data = event.get("type"), event["data"]
    if kind == "tool/call":
        call_id = data.get("callId")
        if not isinstance(call_id, str) or not call_id:
            return None
        name = data.get("name")
        if not isinstance(name, str) or not name:
            name = "tool"
        arguments = _public_tool_arguments(name, data.get("arguments"))
        return "tool_call", {"id": call_id, "name": name, "arguments": arguments}
    if kind == "tool/result":
        output, is_error, call_id = telemetry._tool_output(data)
        if not call_id:
            return None
        return "tool_result", {"id": call_id, "result": output, "is_error": is_error}
    if kind == "assistant/message":
        message = data.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        text = "".join(
            str(block.get("text") or "")
            for block in blocks or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if text:
            return "assistant", {"text": text}
        reasoning = "".join(
            str(block.get("text") or "")
            for block in blocks or []
            if isinstance(block, dict) and block.get("type") == "reasoning"
        ).strip()
        if reasoning:
            if reasoning.startswith("**") and reasoning.endswith("**"):
                reasoning = reasoning[2:-2]
            return "reasoning", {"text": reasoning, "turn": data.get("turn"), "step": data.get("step")}
    return None


def sse_frame(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def process_upload(
    upload_id: str,
    staging: Path,
    destination: Path,
    jobs: dict[str, dict[str, Any]],
    lock: threading.Lock,
) -> None:
    try:
        with zipfile.ZipFile(staging) as workbook:
            names = set(workbook.namelist())
            if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                raise ValueError("file is not an XLSX workbook")
            if sum(item.file_size for item in workbook.infolist()) > MAX_UNCOMPRESSED_WORKBOOK_BYTES:
                raise ValueError("uncompressed workbook exceeds 256 MB")
            if workbook.testzip() is not None:
                raise ValueError("workbook archive is corrupt")
        staging.replace(destination)
        info = destination.stat()
        result = {
            "id": upload_id,
            "status": "complete",
            "file": {"name": destination.name, "size": info.st_size, "modified_at": info.st_mtime},
        }
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        staging.unlink(missing_ok=True)
        result = {"id": upload_id, "status": "failed", "error": str(error)}
    with lock:
        jobs[upload_id] = result


def chat_events(state: Any, chat_id: uuid.UUID, message: str, history: list[dict[str, Any]]) -> Iterator[bytes]:
    events: queue.Queue[bytes | None] = queue.Queue()

    def emit_frame(kind: str, value: dict[str, Any]) -> None:
        events.put(sse_frame(kind, value))

    def run() -> None:
        emit_frame("status", {"text": "Thinking…"})
        reasoning_by_step: dict[tuple[str, object, object], str] = {}
        try:
            prompt = message if chat_id in state.primed_chats else resumed_prompt(message, history)
            turn_id = uuid.uuid4()
            database.add_message(chat_id, "user", message, turn_id)
            runtime_session_id = f"{chat_id.hex}-{state.harness_process_id}"

            def emit(notification: Notification) -> None:
                event = browser_event(notification)
                if not event:
                    return
                kind, value = event
                agent_session_id = str(notification.payload.get("sessionId") or "unknown")
                if kind == "reasoning":
                    reasoning_by_step[(agent_session_id, value["turn"], value["step"])] = value["text"]
                    return
                if kind == "tool_call":
                    event_data = notification.payload["event"]["data"]
                    value["reasoning"] = reasoning_by_step.get(
                        (agent_session_id, event_data.get("turn"), event_data.get("step"))
                    )
                    database.add_tool_call(
                        chat_id,
                        turn_id,
                        value["id"],
                        agent_session_id,
                        value["name"],
                        value["arguments"],
                        value["reasoning"],
                    )
                elif kind == "tool_result":
                    database.complete_tool_call(chat_id, value["id"], value["result"], value["is_error"])
                if agent_session_id == runtime_session_id or kind in {"tool_call", "tool_result"}:
                    emit_frame(kind, value)

            result = telemetry.run_agent(
                state.harness, prompt, runtime_session_id, str(chat_id), on_notification=emit
            )
            if result.finish_reason == "error" or not result.final_response:
                raise RuntimeError("Agent turn failed")
            state.primed_chats.add(chat_id)
            database.add_message(chat_id, "assistant", result.final_response, turn_id)
            emit_frame("done", {"response": result.final_response, "finish_reason": result.finish_reason})
        except Exception as error:
            emit_frame("error", {"error": str(error)})
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()
    while (event := events.get()) is not None:
        yield event


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(PROJECT_ROOT / ".env")
    provider, model = llm_config(os.environ)
    tracer_provider = telemetry.configure()

    session_root = PROJECT_ROOT / ".dsh" / "sessions"
    session_root.mkdir(parents=True, exist_ok=True)
    workspace = Path(os.environ.get("AGENT_WORKSPACE", PROJECT_ROOT)).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    runtime = ROOT / "node_modules" / ".bin" / "dsh-jsonrpc-agent"
    if not runtime.exists():
        raise RuntimeError("Harness MCP workaround is not installed; run npm install")

    harness = DeepSeekHarness(
        provider=provider,
        model=model,
        cwd=str(workspace),
        session_root=str(session_root),
        cordis=str(ROOT / "poc.cordis.yml"),
        runtime_bin=str(runtime),
        env={
            "MCP_PYTHON": sys.executable,
            "MCP_SERVER": str(ROOT / "mcp_server.py"),
            "DATABASE_URL": "",  # Do not expose app database credentials to Harness/MCP children.
        },
    )
    harness.start()
    # ponytail: rc.6 has no plugin-readiness handshake; give MCP discovery time.
    await asyncio.sleep(5)
    app.state.harness = harness
    app.state.harness_process_id = uuid.uuid4().hex[:12]
    app.state.workspace = workspace
    app.state.primed_chats = set()
    # ponytail: upload status is process-local; persist/prune it if jobs become long-lived.
    app.state.upload_jobs = {}
    app.state.upload_lock = threading.Lock()
    try:
        yield
    finally:
        harness.close()
        tracer_provider.shutdown()


app = FastAPI(title="Harness Chat", lifespan=lifespan)


@app.get("/api/chats")
def list_chats() -> dict[str, Any]:
    return {"chats": database.list_sessions()}


@app.post("/api/chats", status_code=201)
def create_chat() -> dict[str, Any]:
    return database.create_session()


@app.get("/api/chats/{chat_id}/history")
def chat_history(chat_id: uuid.UUID) -> dict[str, Any]:
    if database.get_session(chat_id) is None:
        raise HTTPException(404, "Chat not found")
    return {"messages": database.list_messages(chat_id)}


@app.post("/api/chats/{chat_id}/stream")
def stream_chat(chat_id: uuid.UUID, payload: ChatPayload, request: Request) -> StreamingResponse:
    if database.get_session(chat_id) is None:
        raise HTTPException(404, "Chat not found")
    history = database.list_messages(chat_id)
    return StreamingResponse(
        chat_events(request.app.state, chat_id, payload.message, history),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/files")
def list_files(request: Request) -> dict[str, Any]:
    return {"files": workspace_files(request.app.state.workspace)}


@app.post("/api/files", status_code=202)
async def upload_file(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> JSONResponse:
    try:
        name = workbook_name(file.filename)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error

    upload_id = uuid.uuid4().hex
    staging = request.app.state.workspace / f".upload-{upload_id}.tmp"
    size = 0
    try:
        with staging.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_WORKBOOK_BYTES:
                    raise HTTPException(413, "workbook exceeds 64 MB")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "workbook is empty")
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    job = {"id": upload_id, "status": "processing", "name": name}
    with request.app.state.upload_lock:
        request.app.state.upload_jobs[upload_id] = job
    background_tasks.add_task(
        process_upload,
        upload_id,
        staging,
        request.app.state.workspace / name,
        request.app.state.upload_jobs,
        request.app.state.upload_lock,
    )
    return JSONResponse(
        {"upload": job},
        status_code=202,
        headers={"Location": f"/api/uploads/{upload_id}"},
        background=background_tasks,
    )


@app.get("/api/uploads/{upload_id}")
def upload_status(upload_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.upload_lock:
        upload = request.app.state.upload_jobs.get(upload_id)
        if upload is None:
            raise HTTPException(404, "Upload not found")
        return {"upload": upload.copy()}


@app.get("/api/files/{name}")
def download_file(name: str, request: Request) -> StreamingResponse:
    try:
        path = request.app.state.workspace / workbook_name(name)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise OSError
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except OSError as error:
        raise HTTPException(404, "Workbook not found") from error

    def content() -> Iterator[bytes]:
        with os.fdopen(descriptor, "rb") as workbook:
            while chunk := workbook.read(1024 * 1024):
                yield chunk

    return StreamingResponse(
        content(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Content-Length": str(info.st_size),
        },
    )


app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets", check_dir=False), name="assets")


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIST / "index.html")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
