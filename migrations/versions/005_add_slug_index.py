"""add partial index on entities.slug

Revision ID: 005
Revises: 004
Create Date: 2025-01-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.create_index(
        "idx_entity_slug",
        "entities",
        ["slug"],
        unique=False,
        postgresql_where=sa.text("slug IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_entity_slug", table_name="entities")
