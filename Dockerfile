FROM node:24-bookworm-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/index.html ./
COPY frontend/src ./src
RUN npm run build

FROM node:24-bookworm-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/* \
    && mkdir /workspace && chown 1000:1000 /workspace
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/uv/python
WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock backend/.python-version backend/package.json backend/package-lock.json ./
RUN uv sync --frozen --no-dev && npm ci --omit=dev --no-audit --no-fund

COPY backend/app.py backend/database.py backend/mcp_server.py backend/telemetry.py backend/poc.cordis.yml backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY --from=frontend /app/frontend/dist /app/frontend/dist
ENV PATH="/app/backend/.venv/bin:$PATH"

FROM base AS test
RUN uv sync --frozen
COPY backend/tests ./tests
CMD ["sh", "-c", "alembic upgrade head && pytest -q tests/integration"]

FROM base AS production
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec python app.py --host 0.0.0.0 --port 8000"]

FROM python:3.12-slim-bookworm AS sandbox-runner
RUN apt-get update && apt-get install -y --no-install-recommends bash bubblewrap && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 sandbox && useradd -u 1000 -g sandbox sandbox \
    && mkdir -p /app /workspace /run/sandbox && chown -R sandbox:sandbox /app /workspace /run/sandbox
RUN pip install --no-cache-dir formualizer==0.8.4
COPY --chown=sandbox:sandbox backend/sandbox_runner.py /app/sandbox_runner.py
ENV PYTHONDONTWRITEBYTECODE=1 SANDBOX_SOCKET=/run/sandbox/runner.sock
USER sandbox:sandbox
CMD ["python", "/app/sandbox_runner.py"]
