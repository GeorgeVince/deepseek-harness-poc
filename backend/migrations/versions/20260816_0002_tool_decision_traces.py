"""Persist tool decision traces by chat turn.

Revision ID: 20260816_0002
Revises: 20260816_0001
"""

from alembic import op
import sqlalchemy as sa

revision = "20260816_0002"
down_revision = "20260816_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "turn_id" not in {column["name"] for column in inspector.get_columns("messages")}:
        op.add_column("messages", sa.Column("turn_id", sa.Uuid(), nullable=True))

    if "tool_calls" not in inspector.get_table_names():
        op.create_table(
            "tool_calls",
            sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("turn_id", sa.Uuid(), nullable=False),
            sa.Column("call_id", sa.Text(), nullable=False),
            sa.Column("agent_session_id", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("arguments", sa.Text(), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("is_error", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_id", "call_id", name="uq_tool_calls_session_call_id"),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("tool_calls")}
    if "tool_calls_session_turn_idx" not in indexes:
        op.create_index("tool_calls_session_turn_idx", "tool_calls", ["session_id", "turn_id", "id"])


def downgrade() -> None:
    op.drop_index("tool_calls_session_turn_idx", table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_column("messages", "turn_id")
