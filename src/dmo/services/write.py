from uuid import UUID

from sqlalchemy import tuple_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Classification, Entity, Media
from dmo.models.schemas import (
    ClassificationCreate,
    ClassificationCreateResponse,
    EntityCreate,
    EntityListItem,
    EntityUpdate,
    MediaCreate,
    MediaCreateResponse,
)
from dmo.services.cache import cache_delete_pattern


class EntityError(Exception):
    pass


async def invalidate_entity_caches(entity_id: UUID) -> None:
    """Purge all cache patterns on any entity write."""
    for pattern in (
        "dmo:detail:*",
        "dmo:open_status:*",
        "dmo:search:*",
        "dmo:nearby:*",
        "dmo:map:*",
        "dmo:classifications:*",
        "dmo:categories:*",
    ):
        await cache_delete_pattern(pattern)


async def _set_location(session: AsyncSession, entity_id: UUID, lon: float, lat: float) -> None:
    """Set PostGIS location column for a single entity via raw SQL."""
    await session.execute(
        text("UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid").bindparams(
            lat=lat, lon=lon, eid=entity_id
        )
    )


async def _set_locations_batch(session: AsyncSession, updates: list[tuple[UUID, float, float]]) -> None:
    """Set PostGIS location column for multiple entities via raw SQL."""
    if not updates:
        return

    params = {}
    values = []
    for i, (eid, lon, lat) in enumerate(updates):
        values.append(f"(:p{i}_id, :p{i}_lon, :p{i}_lat)")
        params[f"p{i}_id"] = str(eid)
        params[f"p{i}_lon"] = lon
        params[f"p{i}_lat"] = lat

    query = text(f"""
        UPDATE entities SET location = ST_SetSRID(ST_MakePoint(tmp.lon, tmp.lat), 4326)
        FROM (VALUES {", ".join(values)}) AS tmp(id, lon, lat)
        WHERE entities.id::text = tmp.id
    """)
    await session.execute(query.bindparams(**params))


async def _fetch_entity(session: AsyncSession, entity_id: UUID) -> Entity:
    """Fetch entity by ID for response serialization. Raises EntityError if not found."""
    stmt = select(Entity).where(col(Entity.id) == entity_id)
    result = await session.exec(stmt)
    return result.one()


async def create_entity(
    session: AsyncSession,
    data: EntityCreate,
) -> EntityListItem:
    """Create a new entity with optional location and media/classifications.

    Raises EntityError if source/source_id already exists.
    """
    entity = Entity(
        source=data.source,
        source_id=data.source_id,
        source_url=data.source_url,
        name=data.name,
        slug=data.slug,
        summary=data.summary,
        description=data.description,
        description_format=data.description_format,
        place_type=data.place_type,
        category_class=data.category_class,
        secondary_types=data.secondary_types,
        collection_id=data.collection_id,
        collection_name=data.collection_name,
        collection_slug=data.collection_slug,
        latitude=data.latitude,
        longitude=data.longitude,
        country=data.country,
        region=data.region,
        locality=data.locality,
        region_names=data.region_names,
        address=data.address,
        postal_code=data.postal_code,
        thumbnail_url=data.thumbnail_url,
        icon_url=data.icon_url,
        website=data.website,
        map_screenshot_url=data.map_screenshot_url,
        license=data.license,
        access_type=data.access_type,
        is_reusable=data.is_reusable,
        is_free=data.is_free,
        is_open=data.is_open,
        opens_at=data.opens_at,
        closes_at=data.closes_at,
        opening_hours=data.opening_hours,
        recommended_season=data.recommended_season,
        business_status=data.business_status,
        phone=data.phone,
        email=data.email,
        booking_link=data.booking_link,
        menu_url=data.menu_url,
        order_url=data.order_url,
        reservations_url=data.reservations_url,
        currency=data.currency,
        price_min=data.price_min,
        price_max=data.price_max,
        price_level=data.price_level,
        is_barrier_free=data.is_barrier_free,
        wheelchair_accessible=data.wheelchair_accessible,
        is_featured=data.is_featured,
        favorite_count=data.favorite_count,
        rating=data.rating,
        reviews_count=data.reviews_count,
        attributes=data.attributes,
        is_active=data.is_active,
    )

    session.add(entity)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise EntityError(f"Entity {data.source}/{data.source_id} already exists")

    entity_id = entity.id

    if data.latitude is not None and data.longitude is not None:
        await _set_location(session, entity_id, data.longitude, data.latitude)

    await invalidate_entity_caches(entity_id)
    await session.commit()
    await session.refresh(entity)

    return EntityListItem.model_validate(entity)


async def update_entity(
    session: AsyncSession,
    source: str,
    source_id: str,
    data: EntityUpdate,
) -> EntityListItem:
    """Update an existing entity by source/source_id.

    Only updates fields provided in data. Raises EntityError if not found.
    """
    stmt = select(Entity).where(
        col(Entity.source) == source,
        col(Entity.source_id) == source_id,
    )
    entity = (await session.exec(stmt)).first()
    if not entity:
        raise EntityError(f"Entity {source}/{source_id} not found")

    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        return EntityListItem.model_validate(entity)

    need_location_update = False
    new_lat = entity.latitude
    new_lon = entity.longitude

    if "latitude" in update_data:
        new_lat = update_data["latitude"]
        need_location_update = True
    if "longitude" in update_data:
        new_lon = update_data["longitude"]
        need_location_update = True

    if need_location_update and new_lat is not None and new_lon is not None:
        update_data.pop("latitude", None)
        update_data.pop("longitude", None)
        entity.latitude = new_lat
        entity.longitude = new_lon
    elif "latitude" in update_data:
        update_data.pop("latitude", None)
    elif "longitude" in update_data:
        update_data.pop("longitude", None)

    for field, value in update_data.items():
        setattr(entity, field, value)

    entity_id = entity.id
    await session.flush()

    if need_location_update and new_lat is not None and new_lon is not None:
        await _set_location(session, entity_id, new_lon, new_lat)

    await invalidate_entity_caches(entity_id)
    await session.commit()
    await session.refresh(entity)

    return EntityListItem.model_validate(entity)


async def delete_entity(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> bool:
    """Soft-delete an entity by setting is_active to False.

    Returns False if not found.
    """
    stmt = select(Entity).where(
        col(Entity.source) == source,
        col(Entity.source_id) == source_id,
    )
    entity = (await session.exec(stmt)).first()
    if not entity:
        return False

    entity_id = entity.id
    entity.is_active = False
    await invalidate_entity_caches(entity_id)
    await session.commit()
    return True


async def bulk_upsert(
    session: AsyncSession,
    entities: list[EntityCreate],
) -> list[EntityListItem]:
    """Bulk upsert entities using ON CONFLICT DO UPDATE.

    Serializes operations with advisory lock. Handles concurrent requests
    by re-checking existing entities on IntegrityError.
    """
    if not entities:
        return []

    # Serialize bulk operations to prevent race condition
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)").bindparams(lock_id=1234567890)
    )

    source_ids = [(d.source, d.source_id) for d in entities]

    existing_stmt = select(Entity).where(
        tuple_(Entity.source, Entity.source_id).in_(source_ids)
    )
    existing_rows = (await session.exec(existing_stmt)).all()
    existing_map = {(e.source, e.source_id): e for e in existing_rows}

    location_updates: list[tuple[UUID, float, float]] = []
    result_ids: list[UUID | None] = []
    new_entities: list[Entity] = []
    new_entity_indices: list[int] = []

    for i, data in enumerate(entities):
        key = (data.source, data.source_id)

        if key in existing_map:
            existing = existing_map[key]
            update_data = data.model_dump(exclude_unset=False)

            need_location_update = False
            new_lat = update_data.get("latitude")
            new_lon = update_data.get("longitude")

            if new_lat is not None and new_lon is not None:
                need_location_update = True
                update_data.pop("latitude", None)
                update_data.pop("longitude", None)

            for field, value in update_data.items():
                setattr(existing, field, value)

            if need_location_update and new_lat is not None and new_lon is not None:
                location_updates.append((existing.id, new_lon, new_lat))

            result_ids.append(existing.id)
        else:
            entity = Entity(
                source=data.source,
                source_id=data.source_id,
                source_url=data.source_url,
                name=data.name,
                slug=data.slug,
                summary=data.summary,
                description=data.description,
                description_format=data.description_format,
                place_type=data.place_type,
                category_class=data.category_class,
                secondary_types=data.secondary_types,
                collection_id=data.collection_id,
                collection_name=data.collection_name,
                collection_slug=data.collection_slug,
                latitude=data.latitude,
                longitude=data.longitude,
                country=data.country,
                region=data.region,
                locality=data.locality,
                region_names=data.region_names,
                address=data.address,
                postal_code=data.postal_code,
                thumbnail_url=data.thumbnail_url,
                icon_url=data.icon_url,
                website=data.website,
                map_screenshot_url=data.map_screenshot_url,
                license=data.license,
                access_type=data.access_type,
                is_reusable=data.is_reusable,
                is_free=data.is_free,
                is_open=data.is_open,
                opens_at=data.opens_at,
                closes_at=data.closes_at,
                opening_hours=data.opening_hours,
                recommended_season=data.recommended_season,
                business_status=data.business_status,
                phone=data.phone,
                email=data.email,
                booking_link=data.booking_link,
                menu_url=data.menu_url,
                order_url=data.order_url,
                reservations_url=data.reservations_url,
                currency=data.currency,
                price_min=data.price_min,
                price_max=data.price_max,
                price_level=data.price_level,
                is_barrier_free=data.is_barrier_free,
                wheelchair_accessible=data.wheelchair_accessible,
                is_featured=data.is_featured,
                favorite_count=data.favorite_count,
                rating=data.rating,
                reviews_count=data.reviews_count,
                attributes=data.attributes,
                is_active=data.is_active,
            )

            new_entities.append(entity)
            new_entity_indices.append(i)
            result_ids.append(None)

    if new_entities:
        session.add_all(new_entities)

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        # Re-check which entities now exist (concurrent bulk may have inserted them)
        rechecked = (await session.exec(select(Entity).where(
            tuple_(Entity.source, Entity.source_id).in_(
                [(e.source, e.source_id) for e in new_entities]
            )
        ))).all()
        rechecked_map = {(e.source, e.source_id): e for e in rechecked}

        still_new: list[Entity] = []
        for idx, entity in zip(new_entity_indices, new_entities):
            key = (entity.source, entity.source_id)
            if key in rechecked_map:
                existing = rechecked_map[key]
                for field_name in Entity.model_fields:
                    val = getattr(entity, field_name, None)
                    if field_name not in ("id", "created_at", "updated_at"):
                        setattr(existing, field_name, val)
                result_ids[idx] = existing.id
                if entity.latitude is not None and entity.longitude is not None:
                    location_updates.append((existing.id, entity.longitude, entity.latitude))
            else:
                still_new.append(entity)

        if still_new:
            session.add_all(still_new)
            await session.flush()

    # Resolve IDs for new entities
    for idx, entity in zip(new_entity_indices, new_entities):
        if result_ids[idx] is None:
            result_ids[idx] = entity.id

    # Set location for new entities
    for entity in new_entities:
        if entity.latitude is not None and entity.longitude is not None:
            location_updates.append((entity.id, entity.longitude, entity.latitude))

    if location_updates:
        await _set_locations_batch(session, location_updates)

    for eid in result_ids:
        if eid is not None:
            await invalidate_entity_caches(eid)

    await session.commit()

    fresh_stmt = select(Entity).where(col(Entity.id).in_(result_ids))
    fresh_rows = (await session.exec(fresh_stmt)).all()
    fresh_map = {e.id: e for e in fresh_rows}

    return [EntityListItem.model_validate(fresh_map[eid]) for eid in result_ids]


async def create_media(
    session: AsyncSession,
    data: MediaCreate,
) -> MediaCreateResponse:
    """Create media for an entity. Raises EntityError if entity not found."""
    stmt = select(Entity).where(col(Entity.id) == data.entity_id)
    entity = (await session.exec(stmt)).first()
    if not entity:
        raise EntityError(f"Entity {data.entity_id} not found")

    media = Media(
        entity_id=data.entity_id,
        media_type=data.media_type,
        url=data.url,
        name=data.name,
        keywords=data.keywords,
        copyright_holder=data.copyright_holder,
        publisher=data.publisher,
        width=data.width,
        height=data.height,
        encoding_format=data.encoding_format,
        sort_order=data.sort_order,
        attributions=data.attributions,
        poster_url=data.poster_url,
        is_muted=data.is_muted,
    )
    session.add(media)
    await invalidate_entity_caches(data.entity_id)
    await session.commit()
    await session.refresh(media)
    media_id = media.id

    return MediaCreateResponse(id=media_id, entity_id=str(data.entity_id))


async def delete_media(
    session: AsyncSession,
    media_id: int,
) -> bool:
    """Soft-delete media by ID. Returns False if not found."""
    stmt = select(Media).where(Media.id == media_id, col(Media.is_active))
    media = (await session.exec(stmt)).first()
    if not media:
        return False

    entity_id = media.entity_id
    media.is_active = False
    await invalidate_entity_caches(entity_id)
    await session.commit()
    return True


async def create_classification(
    session: AsyncSession,
    data: ClassificationCreate,
) -> ClassificationCreateResponse:
    """Create classification for an entity. Raises EntityError if entity not found."""
    stmt = select(Entity).where(col(Entity.id) == data.entity_id)
    entity = (await session.exec(stmt)).first()
    if not entity:
        raise EntityError(f"Entity {data.entity_id} not found")

    classif = Classification(
        entity_id=data.entity_id,
        category=data.category,
        value_code=data.value_code,
        value_title=data.value_title,
    )
    session.add(classif)
    await invalidate_entity_caches(data.entity_id)
    await session.commit()
    await session.refresh(classif)

    return ClassificationCreateResponse(id=classif.id, entity_id=str(data.entity_id))


async def delete_classification(
    session: AsyncSession,
    classification_id: int,
) -> bool:
    """Soft-delete classification by ID. Returns False if not found."""
    stmt = select(Classification).where(Classification.id == classification_id, col(Classification.is_active))
    classif = (await session.exec(stmt)).first()
    if not classif:
        return False

    entity_id = classif.entity_id
    classif.is_active = False
    await invalidate_entity_caches(entity_id)
    await session.commit()
    return True
