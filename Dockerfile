FROM node:24-bookworm-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
WORKDIR /app

COPY pyproject.toml uv.lock .python-version package.json package-lock.json ./
RUN uv sync --frozen --no-dev && npm ci --omit=dev --no-audit --no-fund

COPY app.py mcp_server.py poc.cordis.yml ./
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["/app/.venv/bin/python", "app.py", "--host", "0.0.0.0", "--port", "8000"]
