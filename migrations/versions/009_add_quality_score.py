"""add quality_score column to entities

Revision ID: 009
Revises: 008
Create Date: 2026-06-24
"""

from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE entities ADD COLUMN quality_score SMALLINT")


def downgrade() -> None:
    op.execute("ALTER TABLE entities DROP COLUMN quality_score")
