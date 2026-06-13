
from sqlmodel import col, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.database import Entity
from dmo.models.schemas import EntityListItem


async def nearby(
    session: AsyncSession,
    lat: float,
    lon: float,
    radius_km: float,
    source: str | None = None,
    place_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[EntityListItem], int, str | None, bool]:
    radius_m = radius_km * 1000

    where_parts = ["entities.is_active = true"]
    params: dict[str, object] = {}

    if source:
        where_parts.append("entities.source = :src")
        params["src"] = source
    if place_type:
        where_parts.append("entities.place_type = :ptype")
        params["ptype"] = place_type

    cursor_filter = ""
    if cursor:
        from dmo.services.pagination import decode_cursor
        last_id, last_distance = decode_cursor(cursor)
        dist_expr = "(ST_Distance(location, ST_MakePoint(:lon, :lat, 4326)::geography) / 1000.0)"
        cursor_filter = f" AND ({dist_expr} > :cursor_distance OR ({dist_expr} = :cursor_distance AND id > :cursor_id))"
        params["cursor_distance"] = last_distance
        params["cursor_id"] = last_id

    where_clause = " AND ".join(where_parts)

    count_sql = text(f"""
        SELECT COUNT(*) FROM entities
        WHERE {where_clause}
          AND ST_DWithin(location, ST_MakePoint(:lon, :lat, 4326)::geography, :radius_m)
    """)
    count_params: dict[str, object] = {"lon": lon, "lat": lat, "radius_m": radius_m}
    count_params.update(params)
    count_sql = count_sql.bindparams(**count_params)
    count_result = await session.exec(count_sql)
    total = count_result.one()[0]

    fetch_size = page_size + 1

    ids_sql = text(f"""
        SELECT id, (ST_Distance(location, ST_MakePoint(:lon, :lat, 4326)::geography) / 1000.0) AS distance_km
        FROM entities
        WHERE {where_clause}
          AND ST_DWithin(location, ST_MakePoint(:lon, :lat, 4326)::geography, :radius_m)
        {cursor_filter}
        ORDER BY distance_km ASC, id ASC
        LIMIT :limit
    """)
    ids_params: dict[str, object] = {
        "lon": lon, "lat": lat, "radius_m": radius_m,
        "limit": fetch_size,
    }
    ids_params.update(params)
    ids_sql = ids_sql.bindparams(**ids_params)
    ids_result = await session.exec(ids_sql)
    id_distance_pairs = list(ids_result.all())

    if not id_distance_pairs:
        return [], 0, None, False

    has_more = len(id_distance_pairs) > page_size
    id_distance_pairs = id_distance_pairs[:page_size]

    entity_ids = [row[0] for row in id_distance_pairs]
    entities_stmt = select(Entity).where(col(Entity.id).in_(entity_ids))
    entities_result = await session.exec(entities_stmt)
    entities = entities_result.all()

    entity_map = {e.id: e for e in entities}
    items = []
    for eid, distance in id_distance_pairs:
        entity = entity_map.get(eid)
        if entity:
            item = EntityListItem.model_validate(entity)
            item.distance_km = round(distance, 2) if distance else None
            items.append(item)

    next_cursor: str | None = None
    if has_more and items:
        from dmo.services.pagination import encode_cursor
        last = items[-1]
        next_cursor = encode_cursor(last.id, last.distance_km or 0)

    return items, total, next_cursor, has_more


async def map_query(
    session: AsyncSession,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    source: str | None = None,
    place_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[EntityListItem], int, str | None, bool]:
    where_parts = ["entities.is_active = true"]
    params: dict[str, object] = {}

    if source:
        where_parts.append("entities.source = :src")
        params["src"] = source
    if place_type:
        where_parts.append("entities.place_type = :ptype")
        params["ptype"] = place_type

    cursor_filter = ""
    if cursor:
        from dmo.services.pagination import decode_cursor
        last_id, _ = decode_cursor(cursor)
        cursor_filter = " AND id > :cursor_id"
        params["cursor_id"] = last_id

    where_clause = " AND ".join(where_parts)

    count_sql = text(f"""
        SELECT COUNT(*) FROM entities
        WHERE {where_clause}
          AND ST_Intersects(location, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)::geography)
    """)
    count_params: dict[str, object] = {
        "min_lon": min_lon, "min_lat": min_lat,
        "max_lon": max_lon, "max_lat": max_lat,
    }
    count_params.update(params)
    count_sql = count_sql.bindparams(**count_params)
    count_result = await session.exec(count_sql)
    total = count_result.one()[0]

    fetch_size = page_size + 1

    ids_sql = text(f"""
        SELECT id FROM entities
        WHERE {where_clause}
          AND ST_Intersects(location, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)::geography)
        {cursor_filter}
        ORDER BY id ASC
        LIMIT :limit
    """)
    ids_params: dict[str, object] = {
        "min_lon": min_lon, "min_lat": min_lat,
        "max_lon": max_lon, "max_lat": max_lat,
        "limit": fetch_size,
    }
    ids_params.update(params)
    ids_sql = ids_sql.bindparams(**ids_params)
    ids_result = await session.exec(ids_sql)
    id_rows = list(ids_result.all())

    if not id_rows:
        return [], 0, None, False

    has_more = len(id_rows) > page_size
    id_rows = id_rows[:page_size]

    entity_ids = [row[0] for row in id_rows]
    entities_stmt = select(Entity).where(col(Entity.id).in_(entity_ids))
    entities_result = await session.exec(entities_stmt)
    entities = entities_result.all()

    items = [EntityListItem.model_validate(e) for e in entities]

    next_cursor: str | None = None
    if has_more and items:
        from dmo.services.pagination import encode_cursor
        last = items[-1]
        next_cursor = encode_cursor(last.id, last.id)

    return items, total, next_cursor, has_more
