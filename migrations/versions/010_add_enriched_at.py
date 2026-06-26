"""add enriched_at column to entities

Revision ID: 010
Revises: 009
Create Date: 2026-06-24
"""

from alembic import op

revision: str = "010"
down_revision: str | None = "009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE entities ADD COLUMN enriched_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE entities DROP COLUMN enriched_at")
