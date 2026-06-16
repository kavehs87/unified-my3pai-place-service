"""increase country column to varchar 100

Revision ID: 008
Revises: 007
Create Date: 2026-06-16
"""

from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE entities ALTER COLUMN country TYPE VARCHAR(100) USING country::VARCHAR(100)")


def downgrade() -> None:
    op.execute("ALTER TABLE entities ALTER COLUMN country TYPE VARCHAR(10) USING country::VARCHAR(10)")
