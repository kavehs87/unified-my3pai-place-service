from uuid import UUID

from sqlalchemy import tuple_
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Classification, Entity, Media
from dmo.models.schemas import (
    ClassificationCreate,
    EntityCreate,
    EntityListItem,
    EntityUpdate,
    MediaCreate,
)
from dmo.services.cache import cache_delete_pattern


class EntityError(Exception):
    pass


async def invalidate_entity_caches(entity_id: UUID) -> None:
    await cache_delete_pattern("dmo:detail:*")


async def _set_location(session: AsyncSession, entity_id: UUID, lon: float, lat: float) -> None:
    await session.execute(
        text("UPDATE entities SET location = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid").bindparams(
            lat=lat, lon=lon, eid=entity_id
        )
    )


async def _fetch_entity(session: AsyncSession, entity_id: UUID) -> Entity:
    stmt = select(Entity).where(col(Entity.id) == entity_id)
    result = await session.exec(stmt)
    return result.one()


async def create_entity(
    session: AsyncSession,
    data: EntityCreate,
) -> EntityListItem:
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

    await session.commit()
    await session.refresh(entity)

    await invalidate_entity_caches(entity_id)
    return EntityListItem.model_validate(entity)


async def update_entity(
    session: AsyncSession,
    source: str,
    source_id: str,
    data: EntityUpdate,
) -> EntityListItem:
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

    await session.commit()
    await session.refresh(entity)

    await invalidate_entity_caches(entity_id)
    return EntityListItem.model_validate(entity)


async def delete_entity(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> bool:
    stmt = select(Entity).where(
        col(Entity.source) == source,
        col(Entity.source_id) == source_id,
    )
    entity = (await session.exec(stmt)).first()
    if not entity:
        return False

    entity_id = entity.id
    entity.is_active = False
    await session.commit()
    await invalidate_entity_caches(entity_id)
    return True


async def bulk_upsert(
    session: AsyncSession,
    entities: list[EntityCreate],
) -> list[EntityListItem]:
    if not entities:
        return []

    source_ids = [(d.source, d.source_id) for d in entities]

    existing_stmt = select(Entity).where(
        tuple_(Entity.source, Entity.source_id).in_(source_ids)
    )
    existing_rows = (await session.exec(existing_stmt)).all()
    existing_map = {(e.source, e.source_id): e for e in existing_rows}

    location_updates: list[tuple[UUID, float, float]] = []
    new_entities: list[Entity] = []
    new_entity_loc_updates: list[tuple[Entity, float, float]] = []
    result_ids: list[UUID | None] = []
    order: list[tuple[str, str]] = []

    for data in entities:
        key = (data.source, data.source_id)
        order.append(key)

        if key in existing_map:
            existing = existing_map[key]
            update_data = data.model_dump(exclude_unset=False)

            need_location_update = False
            new_lat = existing.latitude
            new_lon = existing.longitude

            if "latitude" in update_data:
                new_lat = update_data["latitude"]
                need_location_update = True
            if "longitude" in update_data:
                new_lon = update_data["longitude"]
                need_location_update = True

            if need_location_update and new_lat is not None and new_lon is not None:
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
            result_ids.append(None)  # placeholder, resolved after flush

            if data.latitude is not None and data.longitude is not None:
                new_entity_loc_updates.append((entity, data.longitude, data.latitude))

    if new_entities:
        session.add_all(new_entities)

    await session.flush()

    # Resolve placeholder IDs for new entities after flush
    if new_entities:
        for i, entity in enumerate(new_entities):
            # Find the placeholder index in result_ids
            for j, rid in enumerate(result_ids):
                if rid is None:
                    result_ids[j] = entity.id
                    break

    # Resolve location updates for new entities
    for entity, lon, lat in new_entity_loc_updates:
        location_updates.append((entity.id, lon, lat))

    if location_updates:
        for entity_id, lon, lat in location_updates:
            await _set_location(session, entity_id, lon, lat)

    await session.commit()

    fresh_stmt = select(Entity).where(col(Entity.id).in_(result_ids))
    fresh_rows = (await session.exec(fresh_stmt)).all()
    fresh_map = {e.id: e for e in fresh_rows}

    results = [EntityListItem.model_validate(fresh_map[eid]) for eid in result_ids]

    for r in results:
        await invalidate_entity_caches(r.id)

    return results


async def create_media(
    session: AsyncSession,
    data: MediaCreate,
) -> dict:
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
    await session.commit()
    await session.refresh(media)
    media_id = media.id

    await invalidate_entity_caches(data.entity_id)
    return {"id": media_id, "entity_id": str(data.entity_id)}


async def delete_media(
    session: AsyncSession,
    media_id: int,
) -> bool:
    stmt = select(Media).where(Media.id == media_id)
    media = (await session.exec(stmt)).first()
    if not media:
        return False

    entity_id = media.entity_id
    await session.delete(media)
    await session.commit()

    await invalidate_entity_caches(entity_id)
    return True


async def create_classification(
    session: AsyncSession,
    data: ClassificationCreate,
) -> dict:
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
    await session.commit()
    await session.refresh(classif)

    await invalidate_entity_caches(data.entity_id)
    return {"id": classif.id, "entity_id": str(data.entity_id)}
