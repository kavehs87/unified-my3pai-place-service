import inspect
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dmo.config import settings


def create_script_engine() -> AsyncEngine:
    """Dedicated engine for admin scripts: long timeout, no statement cache."""
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        isolation_level="READ_COMMITTED",
        connect_args={
            "server_settings": {"statement_timeout": "120000"},
            "prepared_statement_cache_size": 0,
        },
    )


async def notify_progress(progress_callback: Any, pct: float, message: str) -> None:
    """Call a progress callback supporting both sync/async and (pct, msg)/msg signatures."""
    if progress_callback is None:
        return
    try:
        outcome = progress_callback(pct, message)
    except TypeError:
        outcome = progress_callback(message)
    if inspect.isawaitable(outcome):
        await outcome
