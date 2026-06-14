"""add trigram indexes for text search

Revision ID: 003
Revises: 002
Create Date: 2025-01-03
"""


from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_entities_name_trgm")
    op.execute("CREATE INDEX idx_entity_name_trgm ON entities USING gin (name gin_trgm_ops)")
    op.execute("CREATE INDEX idx_entity_summary_trgm ON entities USING gin (summary gin_trgm_ops)")


def downgrade() -> None:
    op.drop_index("idx_entity_summary_trgm")
    op.drop_index("idx_entity_name_trgm")
    op.execute("CREATE INDEX idx_entities_name_trgm ON entities USING gin (name gin_trgm_ops)")
