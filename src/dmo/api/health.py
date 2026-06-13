from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.db import get_session
from dmo.services.cache import get_cache

health_router = APIRouter()


@health_router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    components: dict[str, str] = {}

    try:
        await session.exec(text("SELECT 1"))
        components["database"] = "up"
    except Exception:
        components["database"] = "down"

    try:
        redis: Redis = await get_cache()
        await redis.ping()
        components["redis"] = "up"
    except Exception:
        components["redis"] = "down"

    status = "ok" if all(v == "up" for v in components.values()) else "degraded"
    return {"status": status, "components": components}
