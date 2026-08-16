"""Persist tool decision traces by chat turn.

Revision ID: 20260816_0002
Revises: 20260816_0001
"""

from alembic import op

revision = "20260816_0002"
down_revision = "20260816_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS turn_id UUID")
    op.execute("""
        CREATE TABLE IF NOT EXISTS tool_calls (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            turn_id UUID NOT NULL,
            call_id TEXT NOT NULL,
            agent_session_id TEXT NOT NULL,
            name TEXT NOT NULL,
            arguments TEXT,
            result TEXT,
            is_error BOOLEAN,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE (session_id, call_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS tool_calls_session_turn_idx
        ON tool_calls (session_id, turn_id, id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tool_calls")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS turn_id")
