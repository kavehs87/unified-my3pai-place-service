"""add my3pai_rephrased state table for rephrase_from_source script

Revision ID: 012
Revises: 011
Create Date: 2026-07-03
"""

from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE my3pai_rephrased (
            id           BIGSERIAL PRIMARY KEY,
            source       VARCHAR(100) NOT NULL,
            source_id    VARCHAR(500) NOT NULL,
            entity_id    UUID NOT NULL,
            rephrased_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(source, source_id)
        )
    """)
    op.execute("CREATE INDEX idx_rephrased_source_id ON my3pai_rephrased(source_id)")


def downgrade() -> None:
    op.drop_table("my3pai_rephrased")
