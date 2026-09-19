from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.exceptions import AppError
from dmo.models.database import Entity
from dmo.models.schemas import EntityListItem
from dmo.services.source_filter import get_disabled_sources, source_not_in_clause

_PROMINENCE_WEIGHT = 0.15
_SUMMARY_RELEVANCE_WEIGHT = 0.5
_GEO_WEIGHT = 0.35
_DEFAULT_BIAS_SCALE_KM = 50.0


async def search(
    session: AsyncSession,
    q: str | None = None,
    source: str | None = None,
    place_type: str | None = None,
    unified_category: str | None = None,
    country: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
    fulltext: bool = False,
    lat: float | None = None,
    lon: float | None = None,
    bias_radius_km: float | None = None,
) -> tuple[list[EntityListItem], int, str | None, bool]:
    """Full-text search with filters and cursor pagination.

    Uses pg_trgm for text matching on name field by default.
    When fulltext=True, also searches summary field (slower on cold cache).
    Auto-detects unified_category level (top/leaf) for filtering.
    Optional lat/lon add a soft proximity boost (bias, never a filter).
    Returns (items, total, next_cursor, has_more).
    """
    await get_disabled_sources(session)

    where_parts = ["entities.is_active = true"]
    params: dict[str, object] = {}

    not_in_sql, not_in_params = source_not_in_clause()
    if not_in_sql:
        where_parts.append(not_in_sql)
        params.update(not_in_params)

    if source:
        where_parts.append("entities.source = :src")
        params["src"] = source
    if place_type:
        where_parts.append("entities.place_type = :ptype")
        params["ptype"] = place_type
    if country:
        where_parts.append("entities.country = :country")
        params["country"] = country
    if unified_category:
        from dmo.services.taxonomy import get_category_level

        level = await get_category_level(session, unified_category)
        if level == "top":
            where_parts.append("entities.unified_category = :ucat")
            params["ucat"] = unified_category
        elif level == "leaf":
            where_parts.append("entities.unified_subcategory = :uscat")
            params["uscat"] = unified_category
    if q:
        if fulltext:
            where_parts.append("(entities.name % :query OR entities.summary % :query)")
        else:
            where_parts.append("entities.name % :query")
        params["query"] = q

    cursor_filter = ""
    ranked = bool(q)
    tiered = lat is not None and lon is not None and bias_radius_km is not None
    cursor_tier: int | None = None
    if cursor:
        from dmo.services.pagination import decode_cursor

        last_id, last_sort = decode_cursor(cursor)
        if ranked:
            if isinstance(last_sort, str) and ":" in last_sort:
                tier_part, rank_part = last_sort.split(":", 1)
                cursor_tier = int(tier_part)
                cursor_rank = float(rank_part)
            elif isinstance(last_sort, (int, float)) and not isinstance(last_sort, bool):
                cursor_rank = float(last_sort)
            else:
                raise AppError("Invalid cursor format", "InvalidCursor", 400)
            if tiered != (cursor_tier is not None):
                raise AppError("Invalid cursor format", "InvalidCursor", 400)
            if tiered:
                cursor_filter = (
                    "WHERE (COALESCE(t.within_radius, 0) < :cursor_tier)"
                    " OR (COALESCE(t.within_radius, 0) = :cursor_tier"
                    " AND (t.rank_score < :cursor_rank"
                    " OR (t.rank_score = :cursor_rank AND t.id > :cursor_id)))"
                )
                params["cursor_tier"] = cursor_tier
            else:
                cursor_filter = (
                    "WHERE t.rank_score < :cursor_rank"
                    " OR (t.rank_score = :cursor_rank AND t.id > :cursor_id)"
                )
            params["cursor_rank"] = cursor_rank
            params["cursor_id"] = last_id
        else:
            cursor_filter = (
                " AND (entities.name > :cursor_name"
                " OR (entities.name = :cursor_name AND entities.id > :cursor_id))"
            )
            params["cursor_name"] = last_sort
            params["cursor_id"] = last_id

    where_clause = " AND ".join(where_parts)
    fetch_size = page_size + 1

    if ranked:
        summary_term = (
            " + :summary_weight * similarity(COALESCE(entities.summary, ''), :query)"
            if fulltext
            else ""
        )
        geo_term = ""
        tier_select = ""
        if lat is not None and lon is not None:
            geo_term = (
                " + :geo_weight * CASE WHEN entities.location IS NOT NULL THEN"
                " 1.0 / (1.0 + ST_DistanceSphere("
                "entities.location::geometry,"
                " ST_SetSRID(ST_MakePoint(:bias_lon, :bias_lat), 4326)"
                ") / 1000.0 / :bias_scale_km) ELSE 0.0 END"
            )
            params["geo_weight"] = _GEO_WEIGHT
            params["bias_lon"] = lon
            params["bias_lat"] = lat
            params["bias_scale_km"] = bias_radius_km or _DEFAULT_BIAS_SCALE_KM
            if tiered and bias_radius_km is not None:
                tier_select = (
                    ", CASE WHEN entities.location IS NOT NULL AND ST_DWithin("
                    "entities.location,"
                    " ST_SetSRID(ST_MakePoint(:bias_lon, :bias_lat), 4326)::geography,"
                    " :bias_radius_m) THEN 1 ELSE 0 END AS within_radius"
                )
                params["bias_radius_m"] = bias_radius_km * 1000
        order_clause = (
            "t.within_radius DESC, t.rank_score DESC, t.id ASC"
            if tiered
            else "t.rank_score DESC, t.id ASC"
        )
        rows_sql = text(f"""
            SELECT t.*
            FROM (
                SELECT entities.*,
                       COUNT(*) OVER() AS total,
                       (
                           similarity(entities.name, :query)
                           {summary_term}
                           + :prominence_weight
                             * (COALESCE(entities.quality_score, 0) / 100.0)
                           {geo_term}
                       ) AS rank_score
                       {tier_select}
                FROM entities
                WHERE {where_clause}
            ) t
            {cursor_filter}
            ORDER BY {order_clause}
            LIMIT :limit
        """)
        params["prominence_weight"] = _PROMINENCE_WEIGHT
        if fulltext:
            params["summary_weight"] = _SUMMARY_RELEVANCE_WEIGHT
    else:
        rows_sql = text(f"""
            SELECT entities.*,
                   COUNT(*) OVER() AS total
            FROM entities
            WHERE {where_clause}{cursor_filter}
            ORDER BY entities.name ASC, entities.id ASC
            LIMIT :limit
        """)

    rows_params: dict[str, object] = {"limit": fetch_size}
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
    ranks: list[float | None] = []
    tiers: list[int | None] = []
    for row in rows:
        mapping = {
            k: v
            for k, v in row.items()
            if k not in ("total", "location", "rank_score", "within_radius")
        }
        entity = Entity.model_validate(mapping)
        items.append(EntityListItem.model_validate(entity))
        ranks.append(row.get("rank_score"))
        tiers.append(row.get("within_radius"))

    next_cursor: str | None = None
    if has_more and items:
        from dmo.services.pagination import encode_cursor

        last = items[-1]
        if ranked:
            rank_value = float(ranks[-1] or 0.0)
            if tiered:
                next_cursor = encode_cursor(last.id, f"{int(tiers[-1] or 0)}:{rank_value}")
            else:
                next_cursor = encode_cursor(last.id, rank_value)
        else:
            next_cursor = encode_cursor(last.id, last.name)

    return items, total, next_cursor, has_more
