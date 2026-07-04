"""Disabled source filtering for read queries.

Reads disabled sources from the `data_sources` table and the `DISABLED_SOURCES`
config setting. Provides a shared, in-memory cached set of disabled source names
and helper functions to inject `NOT IN` clauses into raw SQL queries.
"""

from datetime import UTC, datetime

import structlog
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings

logger = structlog.get_logger()

_disabled_sources: set[str] | None = None
_cache_time: datetime | None = None
_CACHE_TTL = 30


async def get_disabled_sources(session: AsyncSession) -> set[str]:
    """Return the set of disabled source names.

    Combines DB-backed disabled sources (is_enabled=false) with config-level
    overrides (DISABLED_SOURCES env var). Uses a short in-memory cache to avoid
    repeated DB lookups.
    """
    global _disabled_sources, _cache_time

    now = datetime.now(UTC)
    if _disabled_sources is not None and _cache_time is not None:
        if (now - _cache_time).total_seconds() < _CACHE_TTL:
            return _disabled_sources

    result = await session.execute(text("SELECT source FROM data_sources WHERE is_enabled = FALSE"))
    db_disabled = {row[0] for row in result.fetchall()}

    config_disabled = set(settings.disabled_sources) if settings.disabled_sources else set()

    _disabled_sources = db_disabled | config_disabled
    _cache_time = now

    if _disabled_sources:
        logger.info("disabled_sources_loaded", sources=sorted(_disabled_sources))

    return _disabled_sources


def invalidate_cache() -> None:
    """Clear the in-memory cache so the next call re-reads from the DB."""
    global _disabled_sources, _cache_time
    _disabled_sources = None
    _cache_time = None


def is_source_disabled(source: str) -> bool:
    """Check if a source is disabled using the cached set.

    Safe to call before the cache is loaded — returns False if cache is empty.
    """
    if _disabled_sources is None:
        return False
    return source in _disabled_sources


def source_not_in_clause(param_prefix: str = "disabled") -> tuple[str, dict[str, str]]:
    """Return SQL fragment and params for filtering out disabled sources.

    Returns ("", {}) if no sources are disabled.
    Returns ("entities.source NOT IN (:d0, :d1)", {"d0": "rexby", "d1": "other"}) otherwise.
    """
    if not _disabled_sources:
        return "", {}

    sources = sorted(_disabled_sources)
    params = {f"{param_prefix}_{i}": s for i, s in enumerate(sources)}
    placeholders = ", ".join(f":{k}" for k in params)
    return f"entities.source NOT IN ({placeholders})", params
