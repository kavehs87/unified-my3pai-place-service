"""register any active entity source missing from data_sources

Revision ID: 016
Revises: 015
Create Date: 2026-09-19
"""

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO data_sources (source, is_enabled)
        SELECT DISTINCT source, TRUE
        FROM entities
        WHERE is_active = TRUE
        ON CONFLICT (source) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DELETE FROM data_sources WHERE source = 'opentripmap'")
