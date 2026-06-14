from sqlmodel import text
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
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[EntityListItem], int, str | None, bool]:
    """Find entities within radius of a point, sorted by distance.

    Uses ST_DWithin for spatial filtering and ST_Distance for sorting.
    Returns (items, total, next_cursor, has_more).
    """
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
        dist_expr = "(ST_Distance(location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) / 1000.0)"
        cursor_filter = f" AND ({dist_expr} > :cursor_distance OR ({dist_expr} = :cursor_distance AND id > :cursor_id))"
        params["cursor_distance"] = last_distance
        params["cursor_id"] = last_id

    where_clause = " AND ".join(where_parts)

    fetch_size = page_size + 1

    rows_sql = text(f"""
        SELECT entities.*,
               (ST_Distance(location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography) / 1000.0) AS distance_km,
               COUNT(*) OVER() AS total
        FROM entities
        WHERE {where_clause}
          AND ST_DWithin(location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)
        {cursor_filter}
        ORDER BY distance_km ASC, id ASC
        LIMIT :limit
    """)
    rows_params: dict[str, object] = {
        "lon": lon,
        "lat": lat,
        "radius_m": radius_m,
        "limit": fetch_size,
    }
    rows_params.update(params)
    rows_sql = rows_sql.bindparams(**rows_params)
    rows_result = await session.exec(rows_sql)
    rows = list(rows_result.mappings().all())

    if not rows:
        return [], 0, None, False

    total = rows[0]["total"]
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    items = []
    row_distances = []
    for row in rows:
        mapping = {k: v for k, v in row.items() if k not in ("distance_km", "total", "location")}
        entity = Entity.model_validate(mapping)
        distance = row["distance_km"]
        item = EntityListItem.model_validate(entity)
        item.distance_km = round(distance, 2) if distance else None
        items.append(item)
        row_distances.append(distance)

    next_cursor: str | None = None
    if has_more and items:
        from dmo.services.pagination import encode_cursor

        last = items[-1]
        next_cursor = encode_cursor(last.id, row_distances[-1] or 0)

    return items, total, next_cursor, has_more


async def map_query(
    session: AsyncSession,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    source: str | None = None,
    place_type: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[list[EntityListItem], int, str | None, bool]:
    """Find entities within a bounding box.

    Uses ST_Intersects with ST_MakeEnvelope for bounding box filtering.
    Returns (items, total, next_cursor, has_more).
    """
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

    fetch_size = page_size + 1

    rows_sql = text(f"""
        SELECT entities.*,
               COUNT(*) OVER() AS total
        FROM entities
        WHERE {where_clause}
          AND ST_Intersects(location, ST_MakeEnvelope(:min_lon, :min_lat, :max_lon, :max_lat, 4326)::geography)
        {cursor_filter}
        ORDER BY id ASC
        LIMIT :limit
    """)
    rows_params: dict[str, object] = {
        "min_lon": min_lon,
        "min_lat": min_lat,
        "max_lon": max_lon,
        "max_lat": max_lat,
        "limit": fetch_size,
    }
    rows_params.update(params)
    rows_sql = rows_sql.bindparams(**rows_params)
    rows_result = await session.exec(rows_sql)
    rows = list(rows_result.mappings().all())

    if not rows:
        return [], 0, None, False

    total = rows[0]["total"]
    has_more = len(rows) > page_size
    rows = rows[:page_size]

    items = []
    for row in rows:
        mapping = {k: v for k, v in row.items() if k not in ("total", "location")}
        entity = Entity.model_validate(mapping)
        items.append(EntityListItem.model_validate(entity))

    next_cursor: str | None = None
    if has_more and items:
        from dmo.services.pagination import encode_cursor

        last = items[-1]
        next_cursor = encode_cursor(last.id, str(last.id))

    return items, total, next_cursor, has_more
