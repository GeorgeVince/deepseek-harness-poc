#!/usr/bin/env python3
"""Browser chat backed by DeepSeek Harness and PostgreSQL."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from deepseek_harness import DeepSeekHarness

import database
import telemetry

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
PAGE = (PROJECT_ROOT / "frontend" / "index.html").read_bytes()
MAX_BODY_BYTES = 20_000
MIN_TOKEN_LIFETIME_MS = 5 * 60 * 1000


def read_pi_oauth(path: Path, now_ms: int) -> str:
    """Return pi's current OpenAI OAuth access token."""
    auth = json.loads(path.read_text())["openai-codex"]
    if auth.get("type") != "oauth" or not isinstance(auth.get("access"), str) or not auth["access"]:
        raise RuntimeError(f"No OpenAI OAuth login in {path}; log in with pi first")
    # ponytail: snapshot pi's token at startup; restart after pi refreshes it.
    if not isinstance(auth.get("expires"), int) or auth["expires"] < now_ms + MIN_TOKEN_LIFETIME_MS:
        raise RuntimeError("Pi's OpenAI OAuth token is expired or expiring; refresh it in pi, then restart")
    return auth["access"]


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


class ChatHandler(BaseHTTPRequestHandler):
    harness: DeepSeekHarness
    harness_process_id: str
    primed_sessions: set[uuid.UUID] = set()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", PAGE)
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
            prompt = message if session_id in self.primed_sessions else resumed_prompt(message, history)
            database.add_message(session_id, "user", message)
            runtime_session_id = f"{session_id.hex}-{self.harness_process_id}"
            result = telemetry.run_agent(self.harness, prompt, runtime_session_id, str(session_id))
            if result.finish_reason == "error" or not result.final_response:
                raise RuntimeError("Agent turn failed")
            self.primed_sessions.add(session_id)
            database.add_message(session_id, "assistant", result.final_response)
            self._json(200, {"response": result.final_response, "finish_reason": result.finish_reason})
        except (ValueError, json.JSONDecodeError) as error:
            self._json(400, {"error": str(error)})
        except Exception as error:
            self._json(500, {"error": str(error)})

    def _json(self, status: int, value: dict[str, Any]) -> None:
        self._send(status, "application/json", json.dumps(value).encode())

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

    tracer_provider = telemetry.configure()

    auth_file = Path(os.environ.get("PI_AUTH_FILE", "~/.pi/agent/auth.json")).expanduser()
    token = read_pi_oauth(auth_file, int(time.time() * 1000))
    session_root = PROJECT_ROOT / ".dsh" / "sessions"
    session_root.mkdir(parents=True, exist_ok=True)

    runtime = ROOT / "node_modules" / ".bin" / "dsh-jsonrpc-agent"
    if not runtime.exists():
        raise RuntimeError("Harness MCP workaround is not installed; run npm install")

    harness = DeepSeekHarness(
        provider="openai-codex",
        model="gpt-5.6-sol",
        cwd=str(PROJECT_ROOT),
        session_root=str(session_root),
        cordis=str(ROOT / "poc.cordis.yml"),
        runtime_bin=str(runtime),
        env={
            "OPENAI_CODEX_TOKEN": token,
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
