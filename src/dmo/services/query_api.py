"""Shared cached query layer used by both the REST router and the MCP tools.

Each function mirrors the cache endpoint name, TTL and fallback semantics of the
original router handlers so REST and MCP share cache entries and the existing
invalidation patterns in ``services/write.py`` keep working.
"""

import json
from typing import Literal

from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.models.schemas import (
    ClassificationListItem,
    CursorPaginatedResponse,
    EntityDetail,
    EntityListItem,
    OpenStatus,
    UnifiedCategoriesResponse,
)
from dmo.services import cache as cache_module
from dmo.services.classifications import list_categories as list_categories_service
from dmo.services.classifications import list_classifications as list_classifications_service
from dmo.services.detail import get_detail as get_detail_service
from dmo.services.detail import get_open_status as get_open_status_service
from dmo.services.search import search as search_service
from dmo.services.spatial import map_query as map_query_service
from dmo.services.spatial import nearby as nearby_service
from dmo.services.taxonomy import list_categories as list_taxonomy_service

CacheStatus = Literal["HIT", "MISS"]


class DetailNotFoundError(Exception):
    """Raised internally so entity misses are never cached."""


async def cached_search(
    session: AsyncSession,
    *,
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
) -> tuple[CursorPaginatedResponse[EntityListItem], str]:
    if lat is not None and lon is not None:
        lat = round(lat, 2)
        lon = round(lon, 2)

    params: dict[str, str | int | float | None] = {
        "q": q,
        "source": source,
        "place_type": place_type,
        "unified_category": unified_category,
        "country": country,
        "page_size": page_size,
        "cursor": cursor,
        "fulltext": fulltext,
        "lat": lat,
        "lon": lon,
        "bias_radius_km": bias_radius_km,
    }

    async def _fetch() -> str:
        items, total, next_cursor, has_more = await search_service(
            session,
            q,
            source,
            place_type,
            unified_category,
            country,
            cursor=cursor,
            page_size=page_size,
            fulltext=fulltext,
            lat=lat,
            lon=lon,
            bias_radius_km=bias_radius_km,
        )
        result = CursorPaginatedResponse[EntityListItem](
            results=items, total=total, next_cursor=next_cursor, has_more=has_more
        )
        return json.dumps(result.model_dump(mode="json"))

    cached, cache_status = await cache_module.cache_get_or_set("search", params, fetch_fn=_fetch)
    if cached:
        return CursorPaginatedResponse[EntityListItem].model_validate(
            json.loads(cached)
        ), cache_status
    fallback_json = await _fetch()
    await cache_module.cache_set_async("search", params, fallback_json)
    return (
        CursorPaginatedResponse[EntityListItem].model_validate(json.loads(fallback_json)),
        cache_status,
    )


async def cached_nearby(
    session: AsyncSession,
    *,
    lat: float,
    lon: float,
    radius_km: float = 10,
    source: str | None = None,
    place_type: str | None = None,
    unified_category: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[CursorPaginatedResponse[EntityListItem], str]:
    params: dict[str, str | int | float | None] = {
        "lat": lat,
        "lon": lon,
        "radius_km": radius_km,
        "source": source,
        "place_type": place_type,
        "unified_category": unified_category,
        "page_size": page_size,
        "cursor": cursor,
    }

    async def _fetch() -> str:
        items, total, next_cursor, has_more = await nearby_service(
            session,
            lat,
            lon,
            radius_km,
            source,
            place_type,
            unified_category,
            cursor=cursor,
            page_size=page_size,
        )
        result = CursorPaginatedResponse[EntityListItem](
            results=items, total=total, next_cursor=next_cursor, has_more=has_more
        )
        return json.dumps(result.model_dump(mode="json"))

    cached, cache_status = await cache_module.cache_get_or_set(
        "nearby", params, fetch_fn=_fetch, ttl=300
    )
    if cached:
        return CursorPaginatedResponse[EntityListItem].model_validate(
            json.loads(cached)
        ), cache_status
    fallback_json = await _fetch()
    await cache_module.cache_set_async("nearby", params, fallback_json, ttl=300)
    return (
        CursorPaginatedResponse[EntityListItem].model_validate(json.loads(fallback_json)),
        cache_status,
    )


async def cached_map(
    session: AsyncSession,
    *,
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    source: str | None = None,
    place_type: str | None = None,
    unified_category: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[CursorPaginatedResponse[EntityListItem], str]:
    bbox = f"{min_lon},{min_lat},{max_lon},{max_lat}"
    params: dict[str, str | int | float | None] = {
        "bbox": bbox,
        "source": source,
        "place_type": place_type,
        "unified_category": unified_category,
        "page_size": page_size,
        "cursor": cursor,
    }

    async def _fetch() -> str:
        items, total, next_cursor, has_more = await map_query_service(
            session,
            min_lon,
            min_lat,
            max_lon,
            max_lat,
            source,
            place_type,
            unified_category,
            cursor=cursor,
            page_size=page_size,
        )
        result = CursorPaginatedResponse[EntityListItem](
            results=items, total=total, next_cursor=next_cursor, has_more=has_more
        )
        return json.dumps(result.model_dump(mode="json"))

    cached, cache_status = await cache_module.cache_get_or_set("map", params, fetch_fn=_fetch)
    if cached:
        return CursorPaginatedResponse[EntityListItem].model_validate(
            json.loads(cached)
        ), cache_status
    fallback_json = await _fetch()
    await cache_module.cache_set_async("map", params, fallback_json)
    return (
        CursorPaginatedResponse[EntityListItem].model_validate(json.loads(fallback_json)),
        cache_status,
    )


async def cached_classification_categories(
    session: AsyncSession,
) -> tuple[list[str], str]:
    async def _fetch() -> str:
        return json.dumps(await list_categories_service(session))

    cached, cache_status = await cache_module.cache_get_or_set("categories", {}, fetch_fn=_fetch)
    if cached:
        return json.loads(cached), cache_status
    fallback_json = await _fetch()
    await cache_module.cache_set_async("categories", {}, fallback_json)
    return json.loads(fallback_json), cache_status


async def cached_classifications(
    session: AsyncSession,
    *,
    entity_id: str | None = None,
    category: str | None = None,
    value_code: str | None = None,
    page_size: int = 20,
    cursor: str | None = None,
) -> tuple[CursorPaginatedResponse[ClassificationListItem], str]:
    params: dict[str, str | int | float | None] = {
        "entity_id": entity_id,
        "category": category,
        "value_code": value_code,
        "page_size": page_size,
        "cursor": cursor,
    }

    async def _fetch() -> str:
        items, total, next_cursor, has_more = await list_classifications_service(
            session, entity_id, category, value_code, cursor=cursor, page_size=page_size
        )
        result = CursorPaginatedResponse[ClassificationListItem](
            results=items, total=total, next_cursor=next_cursor, has_more=has_more
        )
        return json.dumps(result.model_dump(mode="json"))

    cached, cache_status = await cache_module.cache_get_or_set(
        "classifications", params, fetch_fn=_fetch
    )
    if cached:
        return (
            CursorPaginatedResponse[ClassificationListItem].model_validate(json.loads(cached)),
            cache_status,
        )
    fallback_json = await _fetch()
    await cache_module.cache_set_async("classifications", params, fallback_json)
    return (
        CursorPaginatedResponse[ClassificationListItem].model_validate(json.loads(fallback_json)),
        cache_status,
    )


async def cached_unified_categories(
    session: AsyncSession,
) -> tuple[UnifiedCategoriesResponse, str]:
    async def _fetch() -> str:
        categories = await list_taxonomy_service(session)
        return json.dumps(UnifiedCategoriesResponse(categories=categories).model_dump(mode="json"))

    cached, cache_status = await cache_module.cache_get_or_set(
        "unified_categories", {}, fetch_fn=_fetch, ttl=300
    )
    if cached:
        return UnifiedCategoriesResponse.model_validate(json.loads(cached)), cache_status
    fallback_json = await _fetch()
    await cache_module.cache_set_async("unified_categories", {}, fallback_json, ttl=300)
    return UnifiedCategoriesResponse.model_validate(json.loads(fallback_json)), cache_status


async def cached_detail(
    session: AsyncSession,
    source: str,
    source_id: str,
) -> tuple[EntityDetail | None, str]:
    detail_params: dict[str, str | int | float | None] = {"source": source, "source_id": source_id}

    async def _fetch_detail() -> str:
        detail = await get_detail_service(session, source, source_id)
        if not detail:
            raise DetailNotFoundError
        detail_dict = detail.model_dump(mode="json")
        detail_dict["is_open"] = None
        detail_dict["opens_at"] = None
        detail_dict["closes_at"] = None
        return json.dumps(detail_dict)

    try:
        cached, cache_status = await cache_module.cache_get_or_set(
            "detail", detail_params, fetch_fn=_fetch_detail, ttl=1800
        )
    except DetailNotFoundError:
        return None, "MISS"

    if cached:
        detail = EntityDetail.model_validate(json.loads(cached))
    else:
        try:
            detail_json = await _fetch_detail()
        except DetailNotFoundError:
            return None, cache_status
        await cache_module.cache_set_async("detail", detail_params, detail_json, ttl=1800)
        detail = EntityDetail.model_validate(json.loads(detail_json))

    async def _fetch_open_status() -> str:
        open_status = await get_open_status_service(session, source, source_id)
        if not open_status:
            return "null"
        return json.dumps(open_status.model_dump(mode="json"))

    open_cached, open_cache_status = await cache_module.cache_get_or_set(
        "open_status", detail_params, fetch_fn=_fetch_open_status, ttl=60
    )
    open_status: OpenStatus | None = None
    if open_cached and open_cached != "null":
        open_status = OpenStatus.model_validate(json.loads(open_cached))

    if open_status:
        detail.is_open = open_status.is_open
        detail.opens_at = open_status.opens_at
        detail.closes_at = open_status.closes_at

    combined_status = (
        cache_status if cache_status == open_cache_status else f"{cache_status}+{open_cache_status}"
    )
    return detail, combined_status
