"""add indexes on entity_id foreign keys

Revision ID: 007
Revises: 006
Create Date: 2026-06-14
"""


from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_media_entity_id ON media (entity_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_classification_entity_id ON classifications (entity_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_media_entity_id")
    op.execute("DROP INDEX IF EXISTS idx_classification_entity_id")
