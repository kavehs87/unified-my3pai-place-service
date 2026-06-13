"""add is_active column to media and classifications tables

Revision ID: 006
Revises: 005
Create Date: 2026-06-14
"""

from typing import Sequence, Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE media ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
    op.execute("ALTER TABLE classifications ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")


def downgrade() -> None:
    op.execute("ALTER TABLE media DROP COLUMN IF EXISTS is_active")
    op.execute("ALTER TABLE classifications DROP COLUMN IF EXISTS is_active")
