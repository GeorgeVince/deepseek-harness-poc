# DeepSeek Harness Python chatbot POC

A tiny browser chatbot with a Python standard-library HTTP backend, the official `deepseek-harness-sdk`, OpenAI Codex OAuth from pi, and `gpt-5.6-sol`.

## Run

Requirements: [uv](https://docs.astral.sh/uv/) and an existing OpenAI login in pi.

```bash
uv sync
uv run python app.py
```

Open <http://127.0.0.1:8000>.

If pi is not logged in, run pi and use `/login` to connect **OpenAI (ChatGPT Plus/Pro)**, then restart the app. Set `PI_AUTH_FILE` if pi's `auth.json` is somewhere other than `~/.pi/agent/auth.json`.

## Check

```bash
uv run pytest
```

## How it works

`app.py` reads pi's `openai-codex` OAuth access token and starts the SDK's bundled Harness runtime. `poc.cordis.yml` enables Harness's pi-ai adapter, exposes only `gpt-5.6-sol`, and configures a tool-free chat agent. Conversation logs stay in the ignored `.dsh/sessions/` directory.

The Python SDK controls a bundled Harness subprocess over JSON-RPC; the Harness engine itself is not reimplemented in Python. The OAuth token is snapshotted when the app starts, so restart after pi refreshes or replaces it.
