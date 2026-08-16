"""PostgreSQL schema and small persistence helpers."""

from __future__ import annotations

import os
import uuid
from functools import lru_cache
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine

metadata = sa.MetaData()

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", sa.Uuid(), primary_key=True),
    sa.Column("title", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    sa.Column("role", sa.Text(), nullable=False),
    sa.Column("content", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    sa.CheckConstraint("role IN ('user', 'assistant')", name="messages_role_check"),
)
sa.Index("messages_session_id_id_idx", messages.c.session_id, messages.c.id)


@lru_cache
def engine() -> Engine:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required")
    return sa.create_engine(url, pool_pre_ping=True)


def _session(row: sa.RowMapping) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def create_session() -> dict[str, Any]:
    session_id = uuid.uuid4()
    with engine().begin() as connection:
        row = connection.execute(
            sessions.insert().values(id=session_id, title="New chat").returning(*sessions.c)
        ).mappings().one()
    return _session(row)


def get_session(session_id: uuid.UUID) -> dict[str, Any] | None:
    with engine().connect() as connection:
        row = connection.execute(sa.select(sessions).where(sessions.c.id == session_id)).mappings().one_or_none()
    return None if row is None else _session(row)


def list_sessions() -> list[dict[str, Any]]:
    with engine().connect() as connection:
        rows = connection.execute(sa.select(sessions).order_by(sessions.c.updated_at.desc())).mappings()
        return [_session(row) for row in rows]


def list_messages(session_id: uuid.UUID) -> list[dict[str, Any]]:
    with engine().connect() as connection:
        rows = connection.execute(
            sa.select(messages).where(messages.c.session_id == session_id).order_by(messages.c.id)
        ).mappings()
        return [
            {
                "id": row["id"],
                "role": row["role"],
                "content": row["content"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]


def add_message(session_id: uuid.UUID, role: str, content: str) -> dict[str, Any]:
    if role not in {"user", "assistant"}:
        raise ValueError("invalid message role")
    with engine().begin() as connection:
        row = connection.execute(
            messages.insert().values(session_id=session_id, role=role, content=content).returning(*messages.c)
        ).mappings().one()
        update = sessions.update().where(sessions.c.id == session_id).values(updated_at=sa.func.now())
        if role == "user":
            update = update.where(sessions.c.title == "New chat").values(title=_title(content))
        connection.execute(update)
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"].isoformat(),
    }


def _title(content: str) -> str:
    value = " ".join(content.split())
    return value if len(value) <= 60 else value[:57] + "..."
