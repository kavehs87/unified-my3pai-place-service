import asyncio

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.db import get_session
from dmo.services.cache import get_cache

health_router = APIRouter()

_HEALTH_TIMEOUT = 1.5


@health_router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    components: dict[str, str] = {}

    try:
        await asyncio.wait_for(session.exec(text("SELECT 1")), timeout=_HEALTH_TIMEOUT)
        components["database"] = "up"
    except TimeoutError:
        components["database"] = "timeout"
    except Exception:
        components["database"] = "down"

    try:
        redis: Redis = await get_cache()
        await asyncio.wait_for(redis.ping(), timeout=_HEALTH_TIMEOUT)
        components["redis"] = "up"
    except TimeoutError:
        components["redis"] = "timeout"
    except Exception:
        components["redis"] = "down"

    status = "ok" if all(v == "up" for v in components.values()) else "degraded"
    return JSONResponse(
        status_code=200 if status == "ok" else 503,
        content={"status": status, "components": components},
    )
