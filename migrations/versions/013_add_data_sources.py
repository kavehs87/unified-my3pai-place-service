"""add data_sources table for source enable/disable management

Revision ID: 013
Revises: 012
Create Date: 2026-07-04
"""

from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE data_sources (
            source       VARCHAR(100) PRIMARY KEY,
            is_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Seed existing sources as enabled
    op.execute("""
        INSERT INTO data_sources (source, is_enabled)
        SELECT DISTINCT source, TRUE
        FROM entities
        WHERE is_active = TRUE
        ON CONFLICT (source) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("data_sources")
