"""add_parent_id_to_conversation.

Revision ID: 2f5ee67f43ce
Revises: 2d23eda297fa
Create Date: 2026-02-12 00:00:00.000000

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
import sqlalchemy as sa
import sqlmodel.sql.sqltypes


if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "2f5ee67f43ce"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add parent_id column to conversation table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = {c["name"] for c in inspector.get_columns("conversation")}

    if "parent_id" not in columns:
        op.add_column(
            "conversation",
            sa.Column("parent_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )
        op.create_index(
            op.f("ix_conversation_parent_id"), "conversation", ["parent_id"], unique=False
        )


def downgrade() -> None:
    """Remove parent_id column from conversation table."""
    op.drop_index(op.f("ix_conversation_parent_id"), table_name="conversation")
    op.drop_column("conversation", "parent_id")
