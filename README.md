# DeepSeek Harness Python + FastMCP chatbot POC

A small browser chatbot using:

- the official `deepseek-harness-sdk`
- OpenAI token or API-key authentication and `gpt-5.6-sol`
- a Python FastMCP server
- MCP tool discovery behind a fixed `search_tools` / `call_tool` interface
- PostgreSQL session/message persistence managed by Alembic
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

Open the chatbot at <http://127.0.0.1:8000> and Phoenix at <http://127.0.0.1:6006>. Compose stores chats in `postgres_data`, traces in `phoenix_data`, and Harness conversation logs in `.dsh/`. Alembic migrations run automatically when the chatbot starts. FastMCP runs as a stdio child process inside that container, so it does not need a separate service.

## Run locally

Requirements: [uv](https://docs.astral.sh/uv/), Node.js/npm, and a configured root `.env` file.

```bash
docker compose up -d postgres phoenix
cd backend
export DATABASE_URL=postgresql+psycopg://chatbot:chatbot@localhost/chatbot
uv sync
npm install
uv run alembic upgrade head
uv run python app.py
```

The backend loads `../.env` automatically when run from `backend/`.

## Arize Phoenix

Phoenix is hosted locally by Compose with persistent SQLite storage and no account or API key. The backend sends only OpenInference LLM workflow traces to the `deepseek-harness-poc` project—ordinary HTTP requests and database queries are not traced.

Each chat turn produces an `AGENT` span containing one `LLM` span per model step and one `TOOL` span per Harness tool call. Model/provider, token counts, finish reason, session ID, and tool status are recorded. Inputs, outputs, tool arguments, and tool results are captured by default, matching Phoenix's OpenAI Agents SDK integration; set `PHOENIX_CAPTURE_CONTENT=false` to redact that content.

## Layout

- `frontend/` — persistent-session browser UI
- `backend/` — Python API, FastMCP tools, database schema, Alembic migrations, and unit/integration tests
- `compose.yml` — chatbot, PostgreSQL, and local Arize Phoenix services
- `compose.test.yml` — Docker Compose integration-test runner

## Tool flow

```text
Agent
  -> mcp__python__search_tools
  -> FastMCP discovers and ranks the private Python tool catalog
  -> returns a one-use search_id
  -> mcp__python__call_tool(search_id, ...)
  -> selected Python function
```

`mcp_server.py` contains five example Python tools. Harness sees only the gateway's two stable tools, preventing every private schema from consuming model context. `call_tool` rejects calls without a valid one-use `search_id`, enforcing search before execution.

The npm JSON-RPC runtime is a temporary workaround: the `0.1.0rc6` Python runtime wheel omits `@deepseek-ai/dsh-mcp-client`. The application still uses the Python SDK; switch back to its bundled runtime once a wheel containing the MCP client is released.

The frontend restores the selected session and reloads its messages from PostgreSQL. After a backend restart, the first turn replays that PostgreSQL transcript into a fresh Harness runtime session; Harness's own logs remain in ignored `.dsh/sessions/`. Restart the backend after changing credentials.

## Test

```bash
make test-unit         # local, no services required
make test-integration  # isolated PostgreSQL via Docker Compose
make test              # both
```

Unit tests live in `backend/tests/unit/`. Integration tests live in `backend/tests/integration/`; they run only in the Compose test container, after applying Alembic migrations to a fresh PostgreSQL volume.
