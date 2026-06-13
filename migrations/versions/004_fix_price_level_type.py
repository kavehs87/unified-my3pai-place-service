"""fix price_level column type from Numeric to Integer

Revision ID: 004
Revises: 003
Create Date: 2025-01-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE entities ALTER COLUMN price_level TYPE INTEGER USING price_level::INTEGER")


def downgrade() -> None:
    op.execute("ALTER TABLE entities ALTER COLUMN price_level TYPE NUMERIC USING price_level::NUMERIC")
