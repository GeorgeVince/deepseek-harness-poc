"""Create chat sessions and messages.

Revision ID: 20260816_0001
Revises:
"""

from alembic import op
import sqlalchemy as sa

revision = "20260816_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ponytail: conditionals adopt databases created by the retired startup schema initializer.
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "sessions" not in tables:
        op.create_table(
            "sessions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if "messages" not in tables:
        op.create_table(
            "messages",
            sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.CheckConstraint("role IN ('user', 'assistant')", name="messages_role_check"),
            sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("messages")}
    if "messages_session_id_id_idx" not in indexes:
        op.create_index("messages_session_id_id_idx", "messages", ["session_id", "id"])


def downgrade() -> None:
    op.drop_index("messages_session_id_id_idx", table_name="messages")
    op.drop_table("messages")
    op.drop_table("sessions")
