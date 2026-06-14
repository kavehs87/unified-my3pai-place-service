"""initial schema

Revision ID: 001
Revises:
Create Date: 2025-01-01
"""


import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "entities",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(500), nullable=False),
        sa.Column("source_url", sa.String(2048)),
        sa.Column("name", sa.String(1000), nullable=False),
        sa.Column("slug", sa.String(500)),
        sa.Column("summary", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("description_format", sa.String(50)),
        sa.Column("place_type", sa.String(100), nullable=False),
        sa.Column("category_class", sa.String(50)),
        sa.Column("secondary_types", sa.ARRAY(sa.Text)),
        sa.Column("collection_id", sa.String(255)),
        sa.Column("collection_name", sa.String(500)),
        sa.Column("collection_slug", sa.String(500)),
        sa.Column("location", sa.Text),  # GEOGRAPHY column created via raw SQL below
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("country", sa.String(10)),
        sa.Column("region", sa.String(255)),
        sa.Column("locality", sa.String(255)),
        sa.Column("region_names", sa.ARRAY(sa.String(255))),
        sa.Column("address", sa.String(500)),
        sa.Column("postal_code", sa.String(20)),
        sa.Column("thumbnail_url", sa.String(2048)),
        sa.Column("icon_url", sa.String(2048)),
        sa.Column("website", sa.String(2048)),
        sa.Column("map_screenshot_url", sa.String(2048)),
        sa.Column("license", sa.String(500)),
        sa.Column("access_type", sa.String(32), server_default="UNKNOWN"),
        sa.Column("is_reusable", sa.Boolean, server_default="FALSE"),
        sa.Column("is_free", sa.Boolean, server_default="FALSE"),
        sa.Column("is_open", sa.Boolean),
        sa.Column("opens_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("closes_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("opening_hours", sa.Text),
        sa.Column("recommended_season", sa.String(100)),
        sa.Column("business_status", sa.String(32)),
        sa.Column("phone", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("booking_link", sa.String(2048)),
        sa.Column("menu_url", sa.String(2048)),
        sa.Column("order_url", sa.String(2048)),
        sa.Column("reservations_url", sa.String(2048)),
        sa.Column("currency", sa.String(10)),
        sa.Column("price_min", sa.Numeric(12, 2)),
        sa.Column("price_max", sa.Numeric(12, 2)),
        sa.Column("price_level", sa.SmallInteger),
        sa.Column("is_barrier_free", sa.Boolean, server_default="FALSE"),
        sa.Column("wheelchair_accessible", sa.Boolean),
        sa.Column("is_featured", sa.Boolean, server_default="FALSE"),
        sa.Column("favorite_count", sa.Integer, server_default="0"),
        sa.Column("rating", sa.Numeric(3, 1)),
        sa.Column("reviews_count", sa.Integer),
        sa.Column("attributes", sa.dialects.postgresql.JSONB, server_default="{}"),
        sa.Column("is_active", sa.Boolean, server_default="TRUE"),
        sa.Column("imported_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # Convert location column to proper GEOGRAPHY type
    op.execute("ALTER TABLE entities ALTER COLUMN location TYPE GEOGRAPHY(POINT, 4326) USING location::GEOGRAPHY(POINT, 4326)")

    op.create_index("idx_entities_location", "entities", ["location"], postgresql_using="gist")
    op.create_index("idx_entities_lat_lon", "entities", ["latitude", "longitude"])
    op.create_index("idx_entities_source", "entities", ["source", "is_active"])
    op.create_index("idx_entities_type", "entities", ["place_type", "is_active"])
    op.create_index("idx_entities_country", "entities", ["country", "is_active"])
    op.create_index("idx_entities_attributes", "entities", ["attributes"], postgresql_using="gin")
    op.create_index("idx_entities_source_unique", "entities", ["source", "source_id"], unique=True)
    op.execute("CREATE INDEX idx_entities_name_trgm ON entities USING GIN (name gin_trgm_ops)")
    op.create_index("idx_entities_collection", "entities", ["collection_id", "is_active"])
    op.create_index("idx_entities_rating", "entities", ["rating"], postgresql_where="rating IS NOT NULL")

    op.create_table(
        "media",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("entity_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_type", sa.String(20), server_default="image", nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("keywords", sa.Text),
        sa.Column("copyright_holder", sa.String(500)),
        sa.Column("publisher", sa.String(255)),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("encoding_format", sa.String(50)),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("attributions", sa.dialects.postgresql.JSONB, server_default="[]"),
        sa.Column("poster_url", sa.String(2048)),
        sa.Column("is_muted", sa.Boolean),
        sa.UniqueConstraint("entity_id", "url", name="uq_media_entity_url"),
    )

    op.create_table(
        "classifications",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("entity_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("value_code", sa.String(100), nullable=False),
        sa.Column("value_title", sa.String(255)),
        sa.UniqueConstraint("entity_id", "category", "value_code", name="uq_classif_entity_value"),
    )

    op.create_table(
        "routes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("entity_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("geometry", sa.Text),  # GEOMETRY column created via raw SQL below
        sa.Column("elevation_profile", sa.dialects.postgresql.JSONB, server_default="[]"),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # Convert geometry column to proper GEOMETRY type
    op.execute("ALTER TABLE routes ALTER COLUMN geometry TYPE GEOMETRY(GEOMETRY, 4326) USING geometry::GEOMETRY(GEOMETRY, 4326)")


def downgrade() -> None:
    op.drop_table("routes")
    op.drop_table("classifications")
    op.drop_table("media")
    op.drop_table("entities")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS postgis")
