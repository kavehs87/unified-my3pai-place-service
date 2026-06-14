from datetime import datetime
from uuid import UUID

from geoalchemy2.elements import WKBElement
from geoalchemy2.types import Geography, Geometry
from pydantic import ConfigDict
from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlmodel import Field, SQLModel


class Entity(SQLModel, table=True):
    __tablename__ = "entities"

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
        )
    )
    source: str = Field(sa_column=Column(String(100), nullable=False))
    source_id: str = Field(sa_column=Column(String(500), nullable=False))
    source_url: str | None = Field(default=None, sa_column=Column(String(2048)))

    name: str = Field(sa_column=Column(String(1000), nullable=False))
    slug: str | None = Field(default=None, sa_column=Column(String(500)))
    summary: str | None = Field(default=None, sa_column=Column(Text))
    description: str | None = Field(default=None, sa_column=Column(Text))
    description_format: str | None = Field(default=None, sa_column=Column(String(50)))

    place_type: str = Field(sa_column=Column(String(100), nullable=False))
    category_class: str | None = Field(default=None, sa_column=Column(String(50)))
    secondary_types: list[str] | None = Field(default=None, sa_column=Column(ARRAY(Text)))

    collection_id: str | None = Field(default=None, sa_column=Column(String(255)))
    collection_name: str | None = Field(default=None, sa_column=Column(String(500)))
    collection_slug: str | None = Field(default=None, sa_column=Column(String(500)))

    location: WKBElement | None = Field(
        default=None, sa_column=Column(Geography(geometry_type="POINT", srid=4326))
    )
    latitude: float | None = Field(default=None, sa_column=Column(Float))
    longitude: float | None = Field(default=None, sa_column=Column(Float))
    country: str | None = Field(default=None, sa_column=Column(String(10)))
    region: str | None = Field(default=None, sa_column=Column(String(255)))
    locality: str | None = Field(default=None, sa_column=Column(String(255)))
    region_names: list[str] | None = Field(default=None, sa_column=Column(ARRAY(String(255))))
    address: str | None = Field(default=None, sa_column=Column(String(500)))
    postal_code: str | None = Field(default=None, sa_column=Column(String(20)))

    thumbnail_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    icon_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    website: str | None = Field(default=None, sa_column=Column(String(2048)))
    map_screenshot_url: str | None = Field(default=None, sa_column=Column(String(2048)))

    license: str | None = Field(default=None, sa_column=Column(String(500)))
    access_type: str | None = Field(default=None, sa_column=Column(String(32)))
    is_reusable: bool = Field(
        default=False, sa_column=Column(Boolean, server_default=text("FALSE"))
    )

    is_free: bool = Field(default=False, sa_column=Column(Boolean, server_default=text("FALSE")))
    is_open: bool | None = Field(default=None, sa_column=Column(Boolean))
    opens_at: datetime | None = Field(default=None, sa_column=Column(TIMESTAMP(timezone=True)))
    closes_at: datetime | None = Field(default=None, sa_column=Column(TIMESTAMP(timezone=True)))
    opening_hours: str | None = Field(default=None, sa_column=Column(Text))
    recommended_season: str | None = Field(default=None, sa_column=Column(String(100)))
    business_status: str | None = Field(default=None, sa_column=Column(String(32)))

    phone: str | None = Field(default=None, sa_column=Column(String(50)))
    email: str | None = Field(default=None, sa_column=Column(String(255)))
    booking_link: str | None = Field(default=None, sa_column=Column(String(2048)))
    menu_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    order_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    reservations_url: str | None = Field(default=None, sa_column=Column(String(2048)))

    currency: str | None = Field(default=None, sa_column=Column(String(10)))
    price_min: float | None = Field(default=None, sa_column=Column(Numeric(12, 2)))
    price_max: float | None = Field(default=None, sa_column=Column(Numeric(12, 2)))
    price_level: int | None = Field(default=None, sa_column=Column(Integer))

    is_barrier_free: bool = Field(
        default=False, sa_column=Column(Boolean, server_default=text("FALSE"))
    )
    wheelchair_accessible: bool | None = Field(default=None, sa_column=Column(Boolean))

    is_featured: bool = Field(
        default=False, sa_column=Column(Boolean, server_default=text("FALSE"))
    )
    favorite_count: int = Field(default=0, sa_column=Column(Integer, server_default=text("0")))
    rating: float | None = Field(default=None, sa_column=Column(Numeric(3, 1)))
    reviews_count: int | None = Field(default=None, sa_column=Column(Integer))

    attributes: dict = Field(
        default_factory=dict, sa_column=Column(JSONB, server_default=text("'{}'"))
    )

    is_active: bool = Field(default=True, sa_column=Column(Boolean, server_default=text("TRUE")))
    imported_at: datetime | None = Field(
        default=None, sa_column=Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
    )
    updated_at: datetime | None = Field(
        default=None, sa_column=Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
    )

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_entity_source_source_id"),
        Index("idx_entity_source_active", "source", "is_active"),
        Index("idx_entity_type_active", "place_type", "is_active"),
        Index("idx_entity_country_active", "country", "is_active"),
        Index("idx_entity_collection_active", "collection_id", "is_active"),
        Index("idx_entity_attributes", "attributes", postgresql_using="gin"),
        Index("idx_entity_rating", "rating", postgresql_where="rating IS NOT NULL"),
        Index(
            "idx_entity_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
        Index(
            "idx_entity_summary_trgm",
            "summary",
            postgresql_using="gin",
            postgresql_ops={"summary": "gin_trgm_ops"},
        ),
        Index("idx_entity_slug", "slug", postgresql_where="slug IS NOT NULL"),
    )


class Media(SQLModel, table=True):
    __tablename__ = "media"

    id: int | None = Field(
        default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True)
    )
    entity_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        )
    )
    media_type: str = Field(
        default="image", sa_column=Column(String(20), server_default=text("'image'"))
    )
    url: str = Field(sa_column=Column(String(2048), nullable=False))
    name: str | None = Field(default=None, sa_column=Column(String(255)))
    keywords: str | None = Field(default=None, sa_column=Column(Text))
    copyright_holder: str | None = Field(default=None, sa_column=Column(String(500)))
    publisher: str | None = Field(default=None, sa_column=Column(String(255)))
    width: int | None = Field(default=None, sa_column=Column(Integer))
    height: int | None = Field(default=None, sa_column=Column(Integer))
    encoding_format: str | None = Field(default=None, sa_column=Column(String(50)))
    sort_order: int = Field(default=0, sa_column=Column(Integer, server_default=text("0")))
    attributions: list = Field(
        default_factory=list, sa_column=Column(JSONB, server_default=text("'[]'"))
    )
    poster_url: str | None = Field(default=None, sa_column=Column(String(2048)))
    is_muted: bool | None = Field(default=None, sa_column=Column(Boolean))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, server_default=text("TRUE")))

    __table_args__ = (
        UniqueConstraint("entity_id", "url", name="uq_media_entity_url"),
        Index("idx_media_entity_id", "entity_id"),
    )


class Classification(SQLModel, table=True):
    __tablename__ = "classifications"

    id: int | None = Field(
        default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True)
    )
    entity_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        )
    )
    category: str = Field(sa_column=Column(String(100), nullable=False))
    value_code: str = Field(sa_column=Column(String(100), nullable=False))
    value_title: str | None = Field(default=None, sa_column=Column(String(255)))
    is_active: bool = Field(default=True, sa_column=Column(Boolean, server_default=text("TRUE")))

    __table_args__ = (
        UniqueConstraint("entity_id", "category", "value_code", name="uq_classif_entity_value"),
        Index("idx_classification_entity_id", "entity_id"),
    )


class Route(SQLModel, table=True):
    __tablename__ = "routes"

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: int | None = Field(
        default=None, sa_column=Column(Integer, primary_key=True, autoincrement=True)
    )
    entity_id: UUID = Field(
        sa_column=Column(
            PG_UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
        )
    )
    geometry: WKBElement | None = Field(
        default=None, sa_column=Column(Geometry(geometry_type="GEOMETRY", srid=4326))
    )
    elevation_profile: list = Field(
        default_factory=list, sa_column=Column(JSONB, server_default=text("'[]'"))
    )
    fetched_at: datetime | None = Field(
        default=None, sa_column=Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
    )
