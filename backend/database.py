"""PostgreSQL schema and small persistence helpers."""

import os
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id UUID PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
        content TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS messages_session_id_id_idx ON messages (session_id, id)",
)


def connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    # ponytail: one connection per operation; add a pool if measured load warrants it.
    return psycopg.connect(url, row_factory=dict_row)


def initialize() -> None:
    with connect() as connection:
        for statement in SCHEMA:
            connection.execute(statement)


def _session(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def create_session() -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            "INSERT INTO sessions (id, title) VALUES (%s, 'New chat') RETURNING *",
            (uuid.uuid4(),),
        ).fetchone()
    return _session(row)


def get_session(session_id: uuid.UUID) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM sessions WHERE id = %s", (session_id,)).fetchone()
    return None if row is None else _session(row)


def list_sessions() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    return [_session(row) for row in rows]


def list_messages(session_id: uuid.UUID) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM messages WHERE session_id = %s ORDER BY id", (session_id,)
        ).fetchall()
    return [
        {
            "id": row["id"],
            "role": row["role"],
            "content": row["content"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


def add_message(session_id: uuid.UUID, role: str, content: str) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO messages (session_id, role, content) VALUES (%s, %s, %s)",
            (session_id, role, content),
        )
        connection.execute(
            """
            UPDATE sessions
            SET updated_at = now(),
                title = CASE WHEN %s = 'user' AND title = 'New chat' THEN %s ELSE title END
            WHERE id = %s
            """,
            (role, _title(content), session_id),
        )


def _title(content: str) -> str:
    value = " ".join(content.split())
    return value if len(value) <= 60 else value[:57] + "..."
