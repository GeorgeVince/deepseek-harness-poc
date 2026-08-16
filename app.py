#!/usr/bin/env python3
"""Tiny browser chat backed by the DeepSeek Harness Python SDK."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from deepseek_harness import DeepSeekHarness

ROOT = Path(__file__).resolve().parent
MAX_BODY_BYTES = 20_000
MIN_TOKEN_LIFETIME_MS = 5 * 60 * 1000

PAGE = b"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Harness + GPT-5.6</title>
<style>
  body { margin: 0; font: 16px system-ui; background: #f5f5f5; color: #171717 }
  main { max-width: 760px; min-height: 100vh; margin: auto; display: grid; grid-template-rows: auto 1fr auto; background: white }
  header { padding: 20px; border-bottom: 1px solid #ddd }
  h1 { margin: 0; font-size: 20px } small { color: #666 }
  #messages { padding: 20px; overflow: auto }
  .message { max-width: 80%; margin: 10px 0; padding: 12px 15px; border-radius: 16px; white-space: pre-wrap }
  .user { margin-left: auto; background: #171717; color: white }
  .assistant { background: #eee }
  form { display: flex; gap: 10px; padding: 16px; border-top: 1px solid #ddd }
  input { flex: 1; padding: 12px; border: 1px solid #aaa; border-radius: 10px; font: inherit }
  button { padding: 0 18px; border: 0; border-radius: 10px; background: #171717; color: white; font: inherit }
  button:disabled { opacity: .5 }
</style>
<main>
  <header><h1>DeepSeek Harness + GPT-5.6 Sol</h1><small>Python SDK / OpenAI OAuth</small></header>
  <section id="messages" aria-live="polite"></section>
  <form><label for="prompt" hidden>Message</label><input id="prompt" autocomplete="off" placeholder="Type a message..." required><button>Send</button></form>
</main>
<script>
const form = document.querySelector('form'), input = document.querySelector('input'), button = document.querySelector('button'), messages = document.querySelector('#messages');
const session_id = crypto.randomUUID();
function add(text, role) { const el = document.createElement('div'); el.className = `message ${role}`; el.textContent = text; messages.append(el); el.scrollIntoView(); return el }
form.addEventListener('submit', async event => {
  event.preventDefault(); const message = input.value.trim(); if (!message) return;
  add(message, 'user'); input.value = ''; button.disabled = input.disabled = true;
  const pending = add('Thinking...', 'assistant');
  try {
    const response = await fetch('/api/chat', { method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify({session_id, message}) });
    const data = await response.json(); if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`); pending.textContent = data.response;
  } catch (error) { pending.textContent = `Error: ${error.message}` }
  finally { button.disabled = input.disabled = false; input.focus() }
});
input.focus();
</script>
</html>
"""


def read_pi_oauth(path: Path, now_ms: int) -> str:
    """Return pi's current OpenAI OAuth access token."""
    auth = json.loads(path.read_text())["openai-codex"]
    if auth.get("type") != "oauth" or not isinstance(auth.get("access"), str) or not auth["access"]:
        raise RuntimeError(f"No OpenAI OAuth login in {path}; log in with pi first")
    # ponytail: snapshot pi's token at startup; restart after pi refreshes it.
    if not isinstance(auth.get("expires"), int) or auth["expires"] < now_ms + MIN_TOKEN_LIFETIME_MS:
        raise RuntimeError("Pi's OpenAI OAuth token is expired or expiring; refresh it in pi, then restart")
    return auth["access"]


def parse_chat_request(raw: bytes) -> tuple[str, str]:
    """Validate one browser request and return its message and session ID."""
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("Request must be a JSON object")
    message, session_id = body.get("message"), body.get("session_id")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required")
    if not isinstance(session_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", session_id) is None:
        raise ValueError("session_id is invalid")
    return message.strip(), session_id


class ChatHandler(BaseHTTPRequestHandler):
    harness: DeepSeekHarness

    def do_GET(self) -> None:
        if self.path != "/":
            self.send_error(404)
            return
        self._send(200, "text/html; charset=utf-8", PAGE)

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            if not 0 < length <= MAX_BODY_BYTES:
                raise ValueError("Invalid request size")
            message, session_id = parse_chat_request(self.rfile.read(length))
            result = self.harness.run(message, session_id=session_id)
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

    auth_file = Path(os.environ.get("PI_AUTH_FILE", "~/.pi/agent/auth.json")).expanduser()
    token = read_pi_oauth(auth_file, int(time.time() * 1000))
    sessions = ROOT / ".dsh" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)

    with DeepSeekHarness(
        provider="openai-codex",
        model="gpt-5.6-sol",
        cwd=str(ROOT),
        session_root=str(sessions),
        cordis=str(ROOT / "poc.cordis.yml"),
        env={"OPENAI_CODEX_TOKEN": token},
    ) as harness:
        ChatHandler.harness = harness
        server = HTTPServer((args.host, args.port), ChatHandler)
        print(f"Chatbot: http://{args.host}:{args.port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
