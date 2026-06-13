import json
from typing import Annotated

from pydantic import TypeAdapter

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings
from dmo.db import get_session
from dmo.models.schemas import (
    ClassificationCreate,
    ClassificationListItem,
    CursorPaginatedResponse,
    EntityCreate,
    EntityDetail,
    EntityListItem,
    EntityUpdate,
    MediaCreate,
)
from dmo.services.cache import cache_get, cache_set_async
from dmo.services.classifications import (
    list_categories as list_categories_service,
)
from dmo.services.classifications import (
    list_classifications as list_classifications_service,
)
from dmo.services.detail import get_detail as get_detail_service
from dmo.services.detail import get_open_status as get_open_status_service
from dmo.services.search import search as search_service
from dmo.services.spatial import map_query as map_query_service
from dmo.services.spatial import nearby as nearby_service
from dmo.services.write import (
    EntityError,
)
from dmo.services.write import (
    bulk_upsert as bulk_upsert_service,
)
from dmo.services.write import (
    create_classification as create_classification_service,
)
from dmo.services.write import (
    create_entity as create_entity_service,
)
from dmo.services.write import (
    create_media as create_media_service,
)
from dmo.services.write import (
    delete_classification as delete_classification_service,
)
from dmo.services.write import (
    delete_entity as delete_entity_service,
)
from dmo.services.write import (
    delete_media as delete_media_service,
)
from dmo.services.write import (
    update_entity as update_entity_service,
)

router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def verify_api_key(x_api_key: str = Header(default="")) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("/search", response_model=CursorPaginatedResponse[EntityListItem])
async def search_endpoint(
    session: SessionDep,
    q: str | None = Query(None, max_length=500),
    source: str | None = Query(None, max_length=200),
    place_type: str | None = Query(None, max_length=200),
    country: str | None = Query(None, max_length=10),
    page_size: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    cached = await cache_get("search", {"q": q, "source": source, "place_type": place_type, "country": country, "page_size": page_size, "cursor": cursor})
    if cached:
        return CursorPaginatedResponse[EntityListItem].model_validate(json.loads(cached))

    items, total, next_cursor, has_more = await search_service(session, q, source, place_type, country, cursor=cursor, page_size=page_size)

    result = CursorPaginatedResponse[EntityListItem](results=items, total=total, next_cursor=next_cursor, has_more=has_more)
    await cache_set_async("search", {"q": q, "source": source, "place_type": place_type, "country": country, "page_size": page_size, "cursor": cursor}, json.dumps(result.model_dump(mode="json")))
    return result


@router.get("/nearby", response_model=CursorPaginatedResponse[EntityListItem])
async def nearby_endpoint(
    session: SessionDep,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(10, gt=0, le=500),
    source: str | None = Query(None, max_length=200),
    place_type: str | None = Query(None, max_length=200),
    page_size: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    cached = await cache_get("nearby", {"lat": lat, "lon": lon, "radius_km": radius_km, "source": source, "place_type": place_type, "page_size": page_size, "cursor": cursor})
    if cached:
        return CursorPaginatedResponse[EntityListItem].model_validate(json.loads(cached))

    items, total, next_cursor, has_more = await nearby_service(session, lat, lon, radius_km, source, place_type, cursor=cursor, page_size=page_size)

    result = CursorPaginatedResponse[EntityListItem](results=items, total=total, next_cursor=next_cursor, has_more=has_more)
    await cache_set_async("nearby", {"lat": lat, "lon": lon, "radius_km": radius_km, "source": source, "place_type": place_type, "page_size": page_size, "cursor": cursor}, json.dumps(result.model_dump(mode="json")), ttl=300)
    return result


@router.get("/map", response_model=CursorPaginatedResponse[EntityListItem])
async def map_endpoint(
    session: SessionDep,
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat", max_length=100),
    source: str | None = Query(None, max_length=200),
    place_type: str | None = Query(None, max_length=200),
    page_size: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    parts = bbox.split(",")
    if len(parts) != 4:
        raise HTTPException(status_code=422, detail="bbox must be minLon,minLat,maxLon,maxLat")
    try:
        min_lon, min_lat, max_lon, max_lat = map(float, parts)
    except ValueError:
        raise HTTPException(status_code=422, detail="bbox values must be numeric")

    if min_lon < -180 or max_lon > 180 or min_lat < -90 or max_lat > 90:
        raise HTTPException(status_code=422, detail="bbox values out of valid coordinate range")
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="bbox min must be less than max")

    cached = await cache_get("map", {"bbox": bbox, "source": source, "place_type": place_type, "page_size": page_size, "cursor": cursor})
    if cached:
        return CursorPaginatedResponse[EntityListItem].model_validate(json.loads(cached))

    items, total, next_cursor, has_more = await map_query_service(session, min_lon, min_lat, max_lon, max_lat, source, place_type, cursor=cursor, page_size=page_size)

    result = CursorPaginatedResponse[EntityListItem](results=items, total=total, next_cursor=next_cursor, has_more=has_more)
    await cache_set_async("map", {"bbox": bbox, "source": source, "place_type": place_type, "page_size": page_size, "cursor": cursor}, json.dumps(result.model_dump(mode="json")))
    return result


@router.get("/classifications/categories", response_model=list[str])
async def categories_endpoint(
    session: SessionDep,
):
    cached = await cache_get("categories", {})
    if cached:
        return TypeAdapter(list[str]).validate_python(json.loads(cached))

    categories = await list_categories_service(session)

    await cache_set_async("categories", {}, json.dumps(categories))
    return categories


@router.get("/classifications", response_model=CursorPaginatedResponse[ClassificationListItem])
async def classifications_endpoint(
    session: SessionDep,
    entity_id: str | None = Query(None, max_length=500),
    category: str | None = Query(None, max_length=200),
    value_code: str | None = Query(None, max_length=200),
    page_size: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    cached = await cache_get("classifications", {"entity_id": entity_id, "category": category, "value_code": value_code, "page_size": page_size, "cursor": cursor})
    if cached:
        return CursorPaginatedResponse[ClassificationListItem].model_validate(json.loads(cached))

    items, total, next_cursor, has_more = await list_classifications_service(session, entity_id, category, value_code, cursor=cursor, page_size=page_size)

    result = CursorPaginatedResponse[ClassificationListItem](results=items, total=total, next_cursor=next_cursor, has_more=has_more)
    await cache_set_async("classifications", {"entity_id": entity_id, "category": category, "value_code": value_code, "page_size": page_size, "cursor": cursor}, json.dumps(result.model_dump(mode="json")))
    return result


@router.get("/{source}/{source_id}", response_model=EntityDetail)
async def detail_endpoint(
    session: SessionDep,
    source: str,
    source_id: str,
):
    detail_params = {"source": source, "source_id": source_id}
    cached = await cache_get("detail", detail_params)

    if cached:
        detail = EntityDetail.model_validate(json.loads(cached))
        open_cached = await cache_get("open_status", detail_params)
        if open_cached:
            from dmo.models.schemas import OpenStatus
            open_status = OpenStatus.model_validate(json.loads(open_cached))
        else:
            open_status = await get_open_status_service(session, source, source_id)
            if open_status:
                await cache_set_async("open_status", detail_params, json.dumps(open_status.model_dump(mode="json")), ttl=60)
        if open_status:
            detail.is_open = open_status.is_open
            detail.opens_at = open_status.opens_at
            detail.closes_at = open_status.closes_at
        return detail

    detail = await get_detail_service(session, source, source_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Entity not found")

    open_status = await get_open_status_service(session, source, source_id)

    await cache_set_async("detail", detail_params, json.dumps(detail.model_dump(mode="json")), ttl=1800)
    if open_status:
        await cache_set_async("open_status", detail_params, json.dumps(open_status.model_dump(mode="json")), ttl=60)

    detail.is_open = open_status.is_open if open_status else detail.is_open
    detail.opens_at = open_status.opens_at if open_status else detail.opens_at
    detail.closes_at = open_status.closes_at if open_status else detail.closes_at
    return detail


@router.post("/entities", status_code=201)
async def create_entity_endpoint(
    session: SessionDep,
    data: EntityCreate,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    try:
        item = await create_entity_service(session, data)
    except EntityError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return item


@router.put("/{source}/{source_id}")
async def update_entity_endpoint(
    session: SessionDep,
    source: str,
    source_id: str,
    data: EntityUpdate,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    try:
        item = await update_entity_service(session, source, source_id, data)
    except EntityError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return item


@router.delete("/media/{media_id}")
async def delete_media_endpoint(
    session: SessionDep,
    media_id: int,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    deleted = await delete_media_service(session, media_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Media not found")
    return {"deleted": True}


@router.delete("/classifications/{classification_id}")
async def delete_classification_endpoint(
    session: SessionDep,
    classification_id: int,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    deleted = await delete_classification_service(session, classification_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Classification not found")
    return {"deleted": True}


@router.delete("/{source}/{source_id}")
async def delete_entity_endpoint(
    session: SessionDep,
    source: str,
    source_id: str,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    deleted = await delete_entity_service(session, source, source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"deleted": True}


@router.post("/entities/bulk", status_code=201)
async def bulk_upsert_endpoint(
    session: SessionDep,
    data: list[EntityCreate],
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    items = await bulk_upsert_service(session, data)
    return items


@router.post("/media", status_code=201)
async def create_media_endpoint(
    session: SessionDep,
    data: MediaCreate,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    try:
        result = await create_media_service(session, data)
    except EntityError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.post("/classifications", status_code=201)
async def create_classification_endpoint(
    session: SessionDep,
    data: ClassificationCreate,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    try:
        result = await create_classification_service(session, data)
    except EntityError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result
