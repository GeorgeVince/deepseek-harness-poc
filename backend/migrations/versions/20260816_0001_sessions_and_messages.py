"""Create chat sessions and messages.

Revision ID: 20260816_0001
Revises:
"""

from alembic import op

revision = "20260816_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS adopts databases created by the retired startup schema initializer.
    op.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id UUID PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS messages_session_id_id_idx ON messages (session_id, id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messages")
    op.execute("DROP TABLE IF EXISTS sessions")
