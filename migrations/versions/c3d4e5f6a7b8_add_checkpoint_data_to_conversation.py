"""Add checkpoint_data JSON column to conversation.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add checkpoint_data JSON column to conversation table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("conversation")}

    if "checkpoint_data" not in existing_columns:
        op.add_column("conversation", sa.Column("checkpoint_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove checkpoint_data JSON column from conversation table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col["name"] for col in inspector.get_columns("conversation")}

    if "checkpoint_data" in existing_columns:
        op.drop_column("conversation", "checkpoint_data")
