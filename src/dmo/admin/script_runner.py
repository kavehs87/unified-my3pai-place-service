import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

logger = structlog.get_logger()


@dataclass
class ScriptRunStatus:
    id: str
    script_name: str
    status: str = "queued"
    progress_pct: float = 0.0
    message: str = ""
    result: dict | None = None
    started_at: str = ""
    finished_at: str | None = None
    error: str | None = None


_runs: dict[str, ScriptRunStatus] = {}
_lock = asyncio.Lock()


async def create_run(script_name: str) -> str:
    run_id = str(uuid.uuid4())
    run = ScriptRunStatus(
        id=run_id,
        script_name=script_name,
        status="queued",
        progress_pct=0.0,
        message="Queued...",
        started_at=datetime.now(UTC).isoformat(),
    )
    async with _lock:
        _runs[run_id] = run
    return run_id


async def update_run(
    run_id: str,
    status: str | None = None,
    progress_pct: float | None = None,
    message: str | None = None,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    async with _lock:
        run = _runs.get(run_id)
        if run is None:
            return
        if status is not None:
            run.status = status
        if progress_pct is not None:
            run.progress_pct = progress_pct
        if message is not None:
            run.message = message
        if result is not None:
            run.result = result
        if error is not None:
            run.error = error
        if status in ("done", "error"):
            run.finished_at = datetime.now(UTC).isoformat()


async def get_run(run_id: str) -> ScriptRunStatus | None:
    async with _lock:
        return _runs.get(run_id)


async def clean_old_runs(max_age_seconds: int = 3600) -> None:
    now = datetime.now(UTC)
    async with _lock:
        stale = []
        for rid, run in _runs.items():
            started = datetime.fromisoformat(run.started_at)
            if (now - started).total_seconds() > max_age_seconds:
                stale.append(rid)
        for rid in stale:
            del _runs[rid]


def make_progress_callback(run_id: str):
    async def callback(pct: float, msg: str, **extra):
        await update_run(run_id, progress_pct=pct, message=msg)
        logger.info("script_progress", run_id=run_id, progress_pct=pct, message=msg, **extra)

    return callback
