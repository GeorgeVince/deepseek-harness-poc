FROM node:24-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html ./
COPY frontend/src ./src
RUN npm run build

FROM node:24-bookworm-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock backend/.python-version backend/package.json backend/package-lock.json ./
RUN uv sync --frozen --no-dev && npm ci --omit=dev --no-audit --no-fund

COPY backend/app.py backend/database.py backend/mcp_server.py backend/telemetry.py backend/poc.cordis.yml ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist
ENV PATH="/app/backend/.venv/bin:$PATH"

FROM base AS test
RUN uv sync --frozen
COPY backend/tests ./tests
CMD ["pytest", "-q", "tests/integration"]

FROM base AS production
EXPOSE 8000
CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "8000"]
