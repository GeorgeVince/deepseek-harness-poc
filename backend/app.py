#!/usr/bin/env python3
"""Browser chat backed by DeepSeek Harness and PostgreSQL."""

import argparse
import json
import mimetypes
import os
import re
import sys
import time
import uuid
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deepseek_harness import DeepSeekHarness, Notification
from dotenv import load_dotenv

import database
import telemetry

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
MAX_BODY_BYTES = 20_000


def llm_config(env: Mapping[str, str]) -> tuple[str, str]:
    token = bool(env.get("OPENAI_TOKEN", "").strip())
    api_key = bool(env.get("OPENAI_API_KEY", "").strip())
    if token == api_key:
        raise RuntimeError("Set exactly one of OPENAI_TOKEN or OPENAI_API_KEY in .env")
    model = env.get("OPENAI_MODEL", "gpt-5.6-sol").strip()
    if not model:
        raise RuntimeError("OPENAI_MODEL cannot be empty")
    return ("openai-codex" if token else "openai"), model


def parse_session_id(value: object) -> uuid.UUID:
    if not isinstance(value, str):
        raise ValueError("session_id is invalid")
    try:
        return uuid.UUID(value)
    except ValueError as error:
        raise ValueError("session_id is invalid") from error


def parse_chat_request(raw: bytes) -> tuple[str, uuid.UUID]:
    """Validate one browser request and return its message and session ID."""
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("Request must be a JSON object")
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required")
    return message.strip(), parse_session_id(body.get("session_id"))


def resumed_prompt(message: str, history: list[dict[str, Any]]) -> str:
    if not history:
        return message
    transcript = [{"role": item["role"], "content": item["content"]} for item in history]
    # ponytail: replay the full DB transcript on first use after restart; compact if histories become large.
    return f"Continue the prior conversation below.\n\n{json.dumps(transcript)}\n\nCurrent user message:\n{message}"


def browser_event(notification: Notification) -> tuple[str, dict[str, Any]] | None:
    """Turn useful Harness notifications into the browser's small event vocabulary."""
    if notification.method != "session.event":
        return None
    event = notification.payload.get("event")
    if not isinstance(event, dict) or not isinstance(event.get("data"), dict):
        return None
    kind, data = event.get("type"), event["data"]
    if kind == "tool/call":
        return "tool_call", {
            "id": data.get("callId"),
            "name": data.get("name") or "tool",
            "arguments": data.get("arguments"),
        }
    if kind == "tool/result":
        output, is_error, call_id = telemetry._tool_output(data)
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
    return None


def sse_frame(event: str, data: dict[str, Any]) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


class ChatHandler(BaseHTTPRequestHandler):
    harness: DeepSeekHarness
    harness_process_id: str
    primed_sessions: set[uuid.UUID] = set()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._static(FRONTEND_DIST / "index.html")
            return
        if re.fullmatch(r"/assets/[A-Za-z0-9._-]+", path):
            self._static(FRONTEND_DIST / path.removeprefix("/"))
            return
        if path == "/api/sessions":
            self._json(200, {"sessions": database.list_sessions()})
            return
        match = re.fullmatch(r"/api/sessions/([^/]+)/messages", path)
        if match:
            try:
                session_id = parse_session_id(match.group(1))
                if database.get_session(session_id) is None:
                    self._json(404, {"error": "Session not found"})
                else:
                    self._json(200, {"messages": database.list_messages(session_id)})
            except ValueError as error:
                self._json(400, {"error": str(error)})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/sessions":
            self._json(201, database.create_session())
            return
        if path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("Invalid request size")
            message, session_id = parse_chat_request(self.rfile.read(length))
            if database.get_session(session_id) is None:
                self._json(404, {"error": "Session not found"})
                return
            history = database.list_messages(session_id)
        except (ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
            return
        except Exception as error:
            self._json(500, {"error": str(error)})
            return

        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.send_header("x-accel-buffering", "no")
        self.end_headers()
        self.close_connection = True
        self._sse("status", {"text": "Thinking…"})
        try:
            prompt = message if session_id in self.primed_sessions else resumed_prompt(message, history)
            database.add_message(session_id, "user", message)
            runtime_session_id = f"{session_id.hex}-{self.harness_process_id}"

            def emit(notification: Notification) -> None:
                if notification.payload.get("sessionId") != runtime_session_id:
                    return
                event = browser_event(notification)
                if event:
                    self._sse(*event)

            result = telemetry.run_agent(
                self.harness, prompt, runtime_session_id, str(session_id), on_notification=emit
            )
            if result.finish_reason == "error" or not result.final_response:
                raise RuntimeError("Agent turn failed")
            self.primed_sessions.add(session_id)
            database.add_message(session_id, "assistant", result.final_response)
            self._sse("done", {"response": result.final_response, "finish_reason": result.finish_reason})
        except Exception as error:
            self._sse("error", {"error": str(error)})

    def _sse(self, event: str, value: dict[str, Any]) -> None:
        try:
            self.wfile.write(sse_frame(event, value))
            self.wfile.flush()
        except OSError:
            pass

    def _json(self, status: int, value: dict[str, Any]) -> None:
        self._send(status, "application/json", json.dumps(value).encode())

    def _static(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.suffix == ".html":
            content_type += "; charset=utf-8"
        self._send(200, content_type, body)

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    provider, model = llm_config(os.environ)
    database.initialize()
    tracer_provider = telemetry.configure()

    session_root = PROJECT_ROOT / ".dsh" / "sessions"
    session_root.mkdir(parents=True, exist_ok=True)

    runtime = ROOT / "node_modules" / ".bin" / "dsh-jsonrpc-agent"
    if not runtime.exists():
        raise RuntimeError("Harness MCP workaround is not installed; run npm install")

    harness = DeepSeekHarness(
        provider=provider,
        model=model,
        cwd=str(PROJECT_ROOT),
        session_root=str(session_root),
        cordis=str(ROOT / "poc.cordis.yml"),
        runtime_bin=str(runtime),
        env={
            "MCP_PYTHON": sys.executable,
            "MCP_SERVER": str(ROOT / "mcp_server.py"),
        },
    )
    # ponytail: rc.6 has no plugin-readiness handshake; give MCP discovery time.
    harness.start()
    time.sleep(5)
    with harness:
        ChatHandler.harness = harness
        ChatHandler.harness_process_id = uuid.uuid4().hex[:12]
        ChatHandler.primed_sessions = set()
        server = HTTPServer((args.host, args.port), ChatHandler)
        print(f"Chatbot: http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    tracer_provider.shutdown()


if __name__ == "__main__":
    main()
