"""Add status column to conversation.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-23

Adds a `status` column to the conversation table to track session
lifecycle state (active, checkpointed, resuming, completed, closed, failed).
This enables soft-delete semantics on session close instead of hard deletion,
allowing sessions to survive server restarts and be resumed by clients.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add status column to conversation table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("conversation")}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("conversation")}

    with op.batch_alter_table("conversation") as batch_op:
        if "status" not in existing_columns:
            batch_op.add_column(
                sa.Column("status", sa.String(), nullable=True, server_default="active")
            )
        if "ix_conversation_status" not in existing_indexes:
            batch_op.create_index("ix_conversation_status", ["status"])

    # Backfill existing rows: treat all current rows as 'active'
    if "status" not in existing_columns:
        conn.execute(sa.text("UPDATE conversation SET status = 'active' WHERE status IS NULL"))


def downgrade() -> None:
    """Remove status column from conversation table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("conversation")}
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("conversation")}

    with op.batch_alter_table("conversation") as batch_op:
        if "ix_conversation_status" in existing_indexes:
            batch_op.drop_index("ix_conversation_status")
        if "status" in existing_columns:
            batch_op.drop_column("status")
