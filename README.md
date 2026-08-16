# DeepSeek Harness Python + FastMCP chatbot POC

A small browser chatbot using:

- the official `deepseek-harness-sdk`
- pi's OpenAI OAuth login and `gpt-5.6-sol`
- a Python FastMCP server
- MCP tool discovery behind a fixed `search_tools` / `call_tool` interface

## Run with Docker Compose

With an existing OpenAI login in pi:

```bash
docker compose up --build
```

Open <http://127.0.0.1:8000>. Compose mounts `~/.pi/agent` read-only for OAuth and persists sessions in `.dsh/`. FastMCP runs as a stdio child process inside the chatbot container, so it does not need a separate Compose service.

## Run locally

Requirements: [uv](https://docs.astral.sh/uv/), Node.js/npm, and an existing OpenAI login in pi.

```bash
uv sync
npm install
uv run python app.py
```

If pi is not logged in, run pi and use `/login` to connect **OpenAI (ChatGPT Plus/Pro)**, then restart the app. Set `PI_AUTH_FILE` for a non-default local auth path; update the Compose volume for a non-default container path.

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

Conversation logs stay in the ignored `.dsh/sessions/` directory. The OAuth token is snapshotted at startup, so restart after pi refreshes it.

## Check

```bash
uv run pytest
```
