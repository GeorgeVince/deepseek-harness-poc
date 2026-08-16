"""Persist provider reasoning summaries for tool calls.

Revision ID: 20260816_0003
Revises: 20260816_0002
"""

from alembic import op

revision = "20260816_0003"
down_revision = "20260816_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS reasoning TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE tool_calls DROP COLUMN IF EXISTS reasoning")
