"""seed my3pai place_type mappings for unified categories

Revision ID: 015
Revises: 014
Create Date: 2026-09-18
"""

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | None = None
depends_on: str | None = None

_NOTES = "data-quality batch 1 (my3pai categories)"


def upgrade() -> None:
    op.execute("""
        INSERT INTO place_type_mappings
            (source, source_place_type, unified_category_id, confidence, is_manual, notes)
        SELECT 'my3pai', v.pt, c.id, v.confidence, TRUE, 'data-quality batch 1 (my3pai categories)'
        FROM (VALUES
            ('experience', 'attraction_leaf', 60),
            ('foodanddrink', 'restaurant', 90),
            ('stay', 'hotel', 90),
            ('transportation', 'station', 70),
            ('guide', 'tour', 80),
            ('unknown', 'attraction_leaf', 30)
        ) AS v(pt, leaf_slug, confidence)
        JOIN unified_categories c ON c.slug = v.leaf_slug
        WHERE NOT EXISTS (
            SELECT 1
            FROM place_type_mappings m
            WHERE m.source = 'my3pai' AND m.source_place_type = v.pt
        )
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM place_type_mappings
        WHERE source = 'my3pai' AND notes = 'data-quality batch 1 (my3pai categories)'
    """)
