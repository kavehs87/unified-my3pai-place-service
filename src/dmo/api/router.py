from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings
from dmo.db import get_session, get_write_session
from dmo.models.schemas import (
    ClassificationCreate,
    ClassificationListItem,
    CursorPaginatedResponse,
    EntityCreate,
    EntityDetail,
    EntityListItem,
    EntityUpdate,
    MediaCreate,
    UnifiedCategoriesResponse,
)
from dmo.services.query_api import (
    cached_classification_categories,
    cached_classifications,
    cached_detail,
    cached_map,
    cached_nearby,
    cached_search,
    cached_unified_categories,
)
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
WriteSessionDep = Annotated[AsyncSession, Depends(get_write_session)]

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(x_api_key: str = Depends(api_key_header)) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@router.get("/search", response_model=CursorPaginatedResponse[EntityListItem], tags=["Read"])
async def search_endpoint(
    session: SessionDep,
    q: str | None = Query(None, max_length=500),
    source: str | None = Query(None, max_length=200),
    place_type: str | None = Query(None, max_length=200),
    unified_category: str | None = Query(None, max_length=200),
    country: str | None = Query(None, max_length=10),
    page_size: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
    fulltext: bool = Query(False, description="Include summary field in text search (slower)"),
    lat: float | None = Query(None, ge=-90, le=90, description="Soft location bias latitude"),
    lon: float | None = Query(None, ge=-180, le=180, description="Soft location bias longitude"),
    bias_radius_km: float | None = Query(
        None, gt=0, le=500, description="Distance scale for the soft location bias"
    ),
):
    if (lat is None) != (lon is None):
        raise HTTPException(status_code=422, detail="lat and lon must be provided together")

    result, cache_status = await cached_search(
        session,
        q=q,
        source=source,
        place_type=place_type,
        unified_category=unified_category,
        country=country,
        page_size=page_size,
        cursor=cursor,
        fulltext=fulltext,
        lat=lat,
        lon=lon,
        bias_radius_km=bias_radius_km,
    )
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"X-Cache-Status": cache_status}
    )


@router.get("/nearby", response_model=CursorPaginatedResponse[EntityListItem], tags=["Read"])
async def nearby_endpoint(
    session: SessionDep,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(10, gt=0, le=500),
    source: str | None = Query(None, max_length=200),
    place_type: str | None = Query(None, max_length=200),
    unified_category: str | None = Query(None, max_length=200),
    page_size: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    result, cache_status = await cached_nearby(
        session,
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        source=source,
        place_type=place_type,
        unified_category=unified_category,
        page_size=page_size,
        cursor=cursor,
    )
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"X-Cache-Status": cache_status}
    )


@router.get("/map", response_model=CursorPaginatedResponse[EntityListItem], tags=["Read"])
async def map_endpoint(
    session: SessionDep,
    bbox: str = Query(..., description="minLon,minLat,maxLon,maxLat", max_length=100),
    source: str | None = Query(None, max_length=200),
    place_type: str | None = Query(None, max_length=200),
    unified_category: str | None = Query(None, max_length=200),
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

    result, cache_status = await cached_map(
        session,
        min_lon=min_lon,
        min_lat=min_lat,
        max_lon=max_lon,
        max_lat=max_lat,
        source=source,
        place_type=place_type,
        unified_category=unified_category,
        page_size=page_size,
        cursor=cursor,
    )
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"X-Cache-Status": cache_status}
    )


@router.get("/classifications/categories", response_model=list[str], tags=["Read"])
async def categories_endpoint(
    session: SessionDep,
):
    result, cache_status = await cached_classification_categories(session)
    return JSONResponse(content=result, headers={"X-Cache-Status": cache_status})


@router.get(
    "/classifications",
    response_model=CursorPaginatedResponse[ClassificationListItem],
    tags=["Read"],
)
async def classifications_endpoint(
    session: SessionDep,
    entity_id: str | None = Query(None, max_length=500),
    category: str | None = Query(None, max_length=200),
    value_code: str | None = Query(None, max_length=200),
    page_size: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=500),
):
    result, cache_status = await cached_classifications(
        session,
        entity_id=entity_id,
        category=category,
        value_code=value_code,
        page_size=page_size,
        cursor=cursor,
    )
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"X-Cache-Status": cache_status}
    )


@router.get("/unified-categories", response_model=UnifiedCategoriesResponse, tags=["Read"])
async def unified_categories_endpoint(
    session: SessionDep,
):
    result, cache_status = await cached_unified_categories(session)
    return JSONResponse(
        content=result.model_dump(mode="json"), headers={"X-Cache-Status": cache_status}
    )


@router.get("/{source}/{source_id}", response_model=EntityDetail, tags=["Read"])
async def detail_endpoint(
    session: SessionDep,
    source: str,
    source_id: str,
):
    detail, cache_status = await cached_detail(session, source, source_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return JSONResponse(
        content=detail.model_dump(mode="json"), headers={"X-Cache-Status": cache_status}
    )


@router.post("/entities", status_code=201, tags=["Write"])
async def create_entity_endpoint(
    session: WriteSessionDep,
    data: EntityCreate,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    try:
        item = await create_entity_service(session, data)
    except EntityError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return item


@router.put("/{source}/{source_id}", tags=["Write"])
async def update_entity_endpoint(
    session: WriteSessionDep,
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


@router.delete("/media/{media_id}", tags=["Write"])
async def delete_media_endpoint(
    session: WriteSessionDep,
    media_id: int,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    deleted = await delete_media_service(session, media_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Media not found")
    return {"deleted": True}


@router.delete("/classifications/{classification_id}", tags=["Write"])
async def delete_classification_endpoint(
    session: WriteSessionDep,
    classification_id: int,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    deleted = await delete_classification_service(session, classification_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Classification not found")
    return {"deleted": True}


@router.delete("/{source}/{source_id}", tags=["Write"])
async def delete_entity_endpoint(
    session: WriteSessionDep,
    source: str,
    source_id: str,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    deleted = await delete_entity_service(session, source, source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"deleted": True}


@router.post("/entities/bulk", status_code=201, tags=["Write"])
async def bulk_upsert_endpoint(
    session: WriteSessionDep,
    data: Annotated[list[EntityCreate], Body(..., max_length=1000)],
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    items = await bulk_upsert_service(session, data)
    return items


@router.post("/media", status_code=201, tags=["Write"])
async def create_media_endpoint(
    session: WriteSessionDep,
    data: MediaCreate,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    try:
        result = await create_media_service(session, data)
    except EntityError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


@router.post("/classifications", status_code=201, tags=["Write"])
async def create_classification_endpoint(
    session: WriteSessionDep,
    data: ClassificationCreate,
    _auth: Annotated[None, Depends(verify_api_key)] = None,
):
    try:
        result = await create_classification_service(session, data)
    except EntityError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result
