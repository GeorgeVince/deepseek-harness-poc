# DeepSeek Harness Python + FastMCP chatbot POC

A small browser chatbot using:

- the official `deepseek-harness-sdk`
- OpenAI token or API-key authentication and `gpt-5.6-sol`
- a Python FastMCP server with UK weather and fictional activity tools
- in-process weather and activities subagents routed by a coordinator
- sandboxed Bash/Python execution in a dedicated agent workspace
- PostgreSQL session/message persistence
- an assistant-ui React frontend
- local OpenTelemetry tracing with Arize Phoenix

## Configure authentication

```bash
cp .env.sample .env
```

Edit `.env` and set exactly one credential:

```dotenv
# OpenAI Codex bearer token
OPENAI_TOKEN=your-token

# Or a standard OpenAI API key
# OPENAI_API_KEY=sk-your-key

OPENAI_MODEL=gpt-5.6-sol
```

A token selects the `openai-codex` route; an API key selects the standard `openai` route. Change `OPENAI_MODEL` if your account does not provide the default model. `.env` is ignored by Git.

## Run with Docker Compose

```bash
docker compose up --build
```

Open the chatbot at <http://127.0.0.1:8000> and Phoenix at <http://127.0.0.1:6006>. Compose stores chats in `postgres_data`, traces in `phoenix_data`, the agent's writable files in `agent_workspace`, and Harness conversation logs in `.dsh/`. Alembic migrations run automatically before the chatbot starts. FastMCP runs as a stdio child process inside the chatbot container and calls the sandbox sidecar over a shared Unix socket.

The coordinator's `run_bash` and `run_python` tools execute in a credential-free sidecar with no network. Its root filesystem is read-only; only `/workspace` and ephemeral `/tmp` are writable. Bubblewrap adds a private PID, mount, IPC, and network namespace per command. Compose also caps the sidecar at one CPU, 256 MB, 64 processes, 30 seconds, 64 MB output files, and a 32 KB returned-output tail. Specialist agents retain their existing tool allowlists and cannot execute code.

[Formualizer](https://github.com/psu3d0/formualizer) 0.8.4 is installed in the sidecar, so the coordinator can load, edit, recalculate, and export XLSX workbooks through `run_python`. Use **+ Excel** in the chat header to upload a workbook, mention its filename in your prompt, and download source or generated workbooks from the adjacent file list. Uploads are limited to simple `.xlsx` filenames and 64 MB.

## Run locally

Requirements: [uv](https://docs.astral.sh/uv/), Node.js/npm, and a configured root `.env` file. The sandbox tools require the Docker Compose setup; a directly launched backend has no local execution fallback.

```bash
docker compose up -d postgres phoenix
cd frontend
npm install
npm run build
cd ../backend
export DATABASE_URL=postgresql://chatbot:chatbot@localhost/chatbot
uv sync
npm install
uv run alembic upgrade head
uv run python app.py
```

The FastAPI backend serves `frontend/dist`, loads `../.env` automatically, and exposes interactive API documentation at `/docs`.

## HTTP API

- `GET|POST /api/chats` — list or create chats
- `GET /api/chats/{chat_id}/history` — persisted messages and decision traces
- `POST /api/chats/{chat_id}/stream` — SSE chat stream
- `GET|POST /api/files` — list workbooks or stage a multipart upload
- `GET /api/files/{name}` — download a workbook
- `GET /api/uploads/{upload_id}` — poll background XLSX validation and publication

Uploads return `202 Accepted`; the background task validates the XLSX archive before making it visible in `/workspace`.

## Arize Phoenix

Phoenix is hosted locally by Compose with persistent SQLite storage and no account or API key. The backend sends only OpenInference LLM workflow traces to the `deepseek-harness-poc` project—ordinary HTTP requests and database queries are not traced.

Each chat turn produces an `AGENT` span containing one `LLM` span per model step and one `TOOL` span per Harness tool call. Model/provider, token counts, finish reason, session ID, and tool status are recorded. Inputs, outputs, tool arguments, and tool results are captured by default, matching Phoenix's OpenAI Agents SDK integration; set `PHOENIX_CAPTURE_CONTENT=false` to redact that content.

## Layout

- `frontend/` — Vite/React persistent-session UI built with assistant-ui
- `backend/` — Python API, FastMCP tools, sandbox runner, Alembic migrations, and tests
- `compose.yml` — chatbot, sandbox sidecar, PostgreSQL, and local Arize Phoenix services
- `compose.test.yml` — Docker Compose integration-test runner

## Agent and tool flow

```text
Coordinator
  ├─ weather request ────> weather agent ────> get_uk_weather
  ├─ activities request ─> activities agent ─┬─> get_uk_weather
  │                                          └─> suggest_uk_activities
  └─ code execution ─────> run_bash/run_python ─> isolated sandbox sidecar
```

The coordinator delegates to fresh in-process Harness agents with separate personas and tool allowlists. `get_uk_weather` uses Open-Meteo for current UK conditions and rough built-in monthly estimates for five cities. `suggest_uk_activities` returns explicitly fictional suggestions for those cities based on weather and season. The old search gateway and unrelated example tools were removed.

The npm JSON-RPC runtime is a temporary workaround: the `0.1.0rc6` Python runtime wheel omits `@deepseek-ai/dsh-mcp-client`. The application still uses the Python SDK; switch back to its bundled runtime once a wheel containing the MCP client is released.

The frontend restores messages and tool calls from PostgreSQL. Tool arguments, results, errors, and nested specialist calls are grouped by turn and rendered as a persisted decision trace; this is observable execution data, not private model chain-of-thought. After a backend restart, the first turn replays the message transcript into a fresh Harness runtime session; Harness's full logs remain in ignored `.dsh/sessions/`. Restart the backend after changing credentials.

## Test

```bash
make test-unit         # local, no services required
make test-integration  # isolated PostgreSQL via Docker Compose
make test              # both
```

Unit tests live in `backend/tests/unit/`. Integration tests live in `backend/tests/integration/`; they run only in the Compose test container after migrating a fresh PostgreSQL service.
