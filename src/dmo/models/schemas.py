from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

T = TypeVar("T")


class CursorPaginatedResponse[T](BaseModel):
    results: list[T]
    total: int
    next_cursor: str | None = None
    has_more: bool = False


class MediaItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: UUID
    media_type: str
    url: str
    name: str | None = None
    keywords: str | None = None
    copyright_holder: str | None = None
    publisher: str | None = None
    width: int | None = None
    height: int | None = None
    encoding_format: str | None = None
    sort_order: int = 0
    attributions: list = []
    poster_url: str | None = None
    is_muted: bool | None = None


class ClassificationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: UUID
    category: str
    value_code: str
    value_title: str | None = None


class ClassificationEntityRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    source_id: str
    name: str
    place_type: str
    thumbnail_url: str | None = None


class ClassificationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_id: UUID
    category: str
    value_code: str
    value_title: str | None = None
    entity: ClassificationEntityRef | None = None


class EntityListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    source_id: str
    name: str
    place_type: str
    summary: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    thumbnail_url: str | None = None
    website: str | None = None
    license: str | None = None
    access_type: str | None = None
    is_reusable: bool = False
    country: str | None = None
    region: str | None = None
    locality: str | None = None
    attributes: dict = {}
    distance_km: float | None = None


class EntityDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    source_id: str
    source_url: str | None = None
    name: str
    slug: str | None = None
    summary: str | None = None
    description: str | None = None
    description_format: str | None = None
    place_type: str
    category_class: str | None = None
    secondary_types: list[str] | None = None
    collection_id: str | None = None
    collection_name: str | None = None
    collection_slug: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = None
    region: str | None = None
    locality: str | None = None
    region_names: list[str] | None = Field(default=None, max_length=100)
    address: str | None = None
    postal_code: str | None = None
    thumbnail_url: str | None = None
    icon_url: str | None = None
    website: str | None = None
    map_screenshot_url: str | None = None
    license: str | None = None
    access_type: str | None = None
    is_reusable: bool = False
    is_free: bool = False
    is_open: bool | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    opening_hours: str | None = None
    recommended_season: str | None = None
    business_status: str | None = None
    phone: str | None = None
    email: str | None = None
    booking_link: str | None = None
    menu_url: str | None = None
    order_url: str | None = None
    reservations_url: str | None = None
    currency: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    price_level: int | None = None
    is_barrier_free: bool = False
    wheelchair_accessible: bool | None = None
    is_featured: bool = False
    favorite_count: int = 0
    rating: float | None = None
    reviews_count: int | None = None
    attributes: dict = {}
    imported_at: datetime | None = None
    updated_at: datetime | None = None
    media: list[MediaItem] = []
    classifications: list[ClassificationItem] = []


class EntityCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str = Field(..., min_length=1, max_length=100)
    source_id: str = Field(..., min_length=1, max_length=500)
    source_url: str | None = Field(None, max_length=2048)
    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    summary: str | None = Field(None, max_length=5000)
    description: str | None = Field(None, max_length=50000)
    description_format: str | None = Field(None, max_length=50)
    place_type: str = Field(..., min_length=1, max_length=100)
    category_class: str | None = Field(None, max_length=100)
    secondary_types: list[str] | None = Field(default=None, max_length=100)
    collection_id: str | None = Field(None, max_length=255)
    collection_name: str | None = Field(None, max_length=255)
    collection_slug: str | None = Field(None, max_length=255)
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=255)
    locality: str | None = Field(None, max_length=255)
    region_names: list[str] | None = Field(default=None, max_length=100)
    address: str | None = Field(None, max_length=500)
    postal_code: str | None = Field(None, max_length=20)
    thumbnail_url: str | None = Field(None, max_length=2048)
    icon_url: str | None = Field(None, max_length=2048)
    website: str | None = Field(None, max_length=2048)
    map_screenshot_url: str | None = Field(None, max_length=2048)
    license: str | None = Field(None, max_length=255)
    access_type: str | None = Field(None, max_length=50)
    is_reusable: bool = False
    is_free: bool = False
    is_open: bool | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    opening_hours: str | None = Field(None, max_length=500)
    recommended_season: str | None = Field(None, max_length=255)
    business_status: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=255)
    booking_link: str | None = Field(None, max_length=2048)
    menu_url: str | None = Field(None, max_length=2048)
    order_url: str | None = Field(None, max_length=2048)
    reservations_url: str | None = Field(None, max_length=2048)
    currency: str | None = Field(None, max_length=3)
    price_min: float | None = None
    price_max: float | None = None
    price_level: int | None = None
    is_barrier_free: bool = False
    wheelchair_accessible: bool | None = None
    is_featured: bool = False
    favorite_count: int = 0
    rating: float | None = None
    reviews_count: int | None = None
    attributes: dict = {}
    is_active: bool = True

    @model_validator(mode='before')
    @classmethod
    def strip_strings(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
        return data

    @model_validator(mode='after')
    def validate_coordinates(self):
        has_lat = self.latitude is not None
        has_lon = self.longitude is not None
        if has_lat != has_lon:
            raise ValueError('Either provide both latitude and longitude, or neither')
        return self


class EntityUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_url: str | None = Field(None, max_length=2048)
    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    summary: str | None = Field(None, max_length=5000)
    description: str | None = Field(None, max_length=50000)
    description_format: str | None = Field(None, max_length=50)
    place_type: str | None = Field(None, min_length=1, max_length=100)
    category_class: str | None = Field(None, max_length=100)
    secondary_types: list[str] | None = Field(default=None, max_length=100)
    collection_id: str | None = Field(None, max_length=255)
    collection_name: str | None = Field(None, max_length=255)
    collection_slug: str | None = Field(None, max_length=255)
    latitude: float | None = None
    longitude: float | None = None
    country: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=255)
    locality: str | None = Field(None, max_length=255)
    region_names: list[str] | None = Field(default=None, max_length=100)
    address: str | None = Field(None, max_length=500)
    postal_code: str | None = Field(None, max_length=20)
    thumbnail_url: str | None = Field(None, max_length=2048)
    icon_url: str | None = Field(None, max_length=2048)
    website: str | None = Field(None, max_length=2048)
    map_screenshot_url: str | None = Field(None, max_length=2048)
    license: str | None = Field(None, max_length=255)
    access_type: str | None = Field(None, max_length=50)
    is_reusable: bool | None = None
    is_free: bool | None = None
    is_open: bool | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    opening_hours: str | None = Field(None, max_length=500)
    recommended_season: str | None = Field(None, max_length=255)
    business_status: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=255)
    booking_link: str | None = Field(None, max_length=2048)
    menu_url: str | None = Field(None, max_length=2048)
    order_url: str | None = Field(None, max_length=2048)
    reservations_url: str | None = Field(None, max_length=2048)
    currency: str | None = Field(None, max_length=3)
    price_min: float | None = None
    price_max: float | None = None
    price_level: int | None = None
    is_barrier_free: bool | None = None
    wheelchair_accessible: bool | None = None
    is_featured: bool | None = None
    favorite_count: int | None = None
    rating: float | None = None
    reviews_count: int | None = None
    attributes: dict | None = None
    is_active: bool | None = None

    @model_validator(mode='before')
    @classmethod
    def strip_strings(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
        return data

    @model_validator(mode='after')
    def validate_coordinates(self):
        has_lat = self.latitude is not None
        has_lon = self.longitude is not None
        if has_lat != has_lon:
            raise ValueError('Either provide both latitude and longitude, or neither')
        return self


class MediaCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    media_type: str = "image"
    url: str
    name: str | None = None
    keywords: str | None = None
    copyright_holder: str | None = None
    publisher: str | None = None
    width: int | None = None
    height: int | None = None
    encoding_format: str | None = None
    sort_order: int = 0
    attributions: list = []
    poster_url: str | None = None
    is_muted: bool | None = None


class ClassificationCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    category: str
    value_code: str
    value_title: str | None = None


class MediaCreateResponse(BaseModel):
    id: int
    entity_id: str


class ClassificationCreateResponse(BaseModel):
    id: int
    entity_id: str


class OpenStatus(BaseModel):
    is_open: bool | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None
