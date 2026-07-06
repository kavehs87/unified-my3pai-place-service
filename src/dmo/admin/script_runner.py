import json
import uuid
from datetime import UTC, datetime

import structlog

from dmo.services.cache import get_cache

logger = structlog.get_logger()

_RUN_TTL = 3600  # 1 hour


def _run_key(run_id: str) -> str:
    return f"dmo:script_run:{run_id}"


async def create_run(script_name: str) -> str:
    run_id = str(uuid.uuid4())
    run = {
        "id": run_id,
        "script_name": script_name,
        "status": "queued",
        "progress_pct": 0.0,
        "message": "Queued...",
        "result": None,
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "error": None,
    }
    client = await get_cache()
    await client.set(_run_key(run_id), json.dumps(run), ex=_RUN_TTL)
    return run_id


async def update_run(
    run_id: str,
    status: str | None = None,
    progress_pct: float | None = None,
    message: str | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    client = await get_cache()
    raw = await client.get(_run_key(run_id))
    if raw is None:
        return
    run = json.loads(raw)
    if status is not None:
        run["status"] = status
    if progress_pct is not None:
        run["progress_pct"] = progress_pct
    if message is not None:
        run["message"] = message
    if result is not None:
        run["result"] = result
    if error is not None:
        run["error"] = error
    if status in ("done", "error"):
        run["finished_at"] = datetime.now(UTC).isoformat()
    await client.set(_run_key(run_id), json.dumps(run), ex=_RUN_TTL)


async def get_run(run_id: str) -> dict | None:
    client = await get_cache()
    raw = await client.get(_run_key(run_id))
    if raw is None:
        return None
    return json.loads(raw)


async def clean_old_runs(max_age_seconds: int = 3600) -> None:
    pass  # Redis handles TTL automatically


def make_progress_callback(run_id: str):

    async def callback(pct: float, msg: str, **extra):
        await update_run(run_id, progress_pct=pct, message=msg)
        logger.info("script_progress", run_id=run_id, progress_pct=pct, message=msg, **extra)

    return callback
