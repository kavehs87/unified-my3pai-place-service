"""Source filtering for read queries.

Reads enabled/disabled sources from the `data_sources` table and the
`DISABLED_SOURCES` config setting. Provides a shared, in-memory cached set
of enabled source names and helper functions to inject `IN` clauses into
raw SQL queries for optimal index usage with partial indexes.
"""

from datetime import UTC, datetime

import structlog
from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.config import settings

logger = structlog.get_logger()

_enabled_sources: set[str] | None = None
_disabled_sources: set[str] | None = None
_cache_time: datetime | None = None
_CACHE_TTL = 30


async def get_disabled_sources(session: AsyncSession) -> set[str]:
    """Return the set of disabled source names.

    Combines DB-backed disabled sources (is_enabled=false) with config-level
    overrides (DISABLED_SOURCES env var). Uses a short in-memory cache to avoid
    repeated DB lookups. Also populates _enabled_sources for IN-clause filtering.
    """
    global _enabled_sources, _disabled_sources, _cache_time

    now = datetime.now(UTC)
    if _disabled_sources is not None and _cache_time is not None:
        if (now - _cache_time).total_seconds() < _CACHE_TTL:
            return _disabled_sources

    result = await session.execute(text("SELECT source, is_enabled FROM data_sources"))
    all_sources = {row[0]: row[1] for row in result.fetchall()}

    db_enabled = {src for src, enabled in all_sources.items() if enabled}
    db_disabled = {src for src, enabled in all_sources.items() if not enabled}

    config_disabled = set(settings.disabled_sources) if settings.disabled_sources else set()

    _enabled_sources = db_enabled - config_disabled
    _disabled_sources = db_disabled | config_disabled
    _cache_time = now

    if _disabled_sources:
        logger.info("disabled_sources_loaded", sources=sorted(_disabled_sources))
    if _enabled_sources:
        logger.info("enabled_sources_loaded", sources=sorted(_enabled_sources))

    return _disabled_sources


def invalidate_cache() -> None:
    """Clear the in-memory cache so the next call re-reads from the DB."""
    global _enabled_sources, _disabled_sources, _cache_time
    _enabled_sources = None
    _disabled_sources = None
    _cache_time = None


def is_source_disabled(source: str) -> bool:
    """Check if a source is disabled using the cached set.

    Safe to call before the cache is loaded — returns False if cache is empty.
    """
    if _disabled_sources is None:
        return False
    return source in _disabled_sources


def source_not_in_clause(param_prefix: str = "enabled") -> tuple[str, dict[str, str]]:
    """Return SQL fragment and params for filtering to only enabled sources.

    Uses IN clause (not NOT IN) to leverage partial indexes on enabled sources.
    Returns ("", {}) if no sources are enabled (all enabled = no filter needed).
    Returns ("entities.source IN (:e0, :e1)", {"e0": "osm", "e1": "my3pai"}) otherwise.
    """
    if not _enabled_sources:
        return "", {}

    sources = sorted(_enabled_sources)
    params = {f"{param_prefix}_{i}": s for i, s in enumerate(sources)}
    placeholders = ", ".join(f":{k}" for k in params)
    return f"entities.source IN ({placeholders})", params
