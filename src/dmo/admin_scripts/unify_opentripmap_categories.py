import inspect
from typing import Any

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult

_RESOLVE_LATERAL = """
    JOIN LATERAL (
        SELECT m.unified_category_id, m.priority, m.kind
        FROM place_kind_mappings m
        WHERE m.source = e2.source
          AND (m.kind = ANY(e2.secondary_types) OR m.kind = e2.place_type)
        ORDER BY m.priority DESC, m.kind ASC
        LIMIT 1
    ) km ON TRUE
    JOIN unified_categories leaf ON leaf.id = km.unified_category_id
    JOIN unified_categories top ON top.id = leaf.parent_id
"""

_DRY_RUN_COUNT_SQL = """
    SELECT COUNT(*)
    FROM entities e
    JOIN LATERAL (
        SELECT m.unified_category_id
        FROM place_kind_mappings m
        WHERE m.source = e.source
          AND (m.kind = ANY(e.secondary_types) OR m.kind = e.place_type)
        ORDER BY m.priority DESC, m.kind ASC
        LIMIT 1
    ) km ON TRUE
    JOIN unified_categories leaf ON leaf.id = km.unified_category_id
    JOIN unified_categories top ON top.id = leaf.parent_id
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND (e.unified_category_id, e.unified_category, e.unified_subcategory)
          IS DISTINCT FROM (km.unified_category_id, top.slug, leaf.slug)
"""

_UNMAPPED_COUNT_SQL = """
    SELECT COUNT(*)
    FROM entities e
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND NOT EXISTS (
          SELECT 1
          FROM place_kind_mappings m
          WHERE m.source = e.source
            AND (m.kind = ANY(e.secondary_types) OR m.kind = e.place_type)
      )
"""

_UNMAPPED_TOKENS_SQL = """
    SELECT tok, COUNT(*) AS cnt
    FROM entities e
    CROSS JOIN LATERAL unnest(
        COALESCE(e.secondary_types, '{}'::varchar[]) || ARRAY[e.place_type]
    ) AS tok
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND NOT EXISTS (
          SELECT 1
          FROM place_kind_mappings m
          WHERE m.source = e.source
            AND (m.kind = ANY(e.secondary_types) OR m.kind = e.place_type)
      )
    GROUP BY tok
    ORDER BY cnt DESC
    LIMIT 20
"""

_SELECT_BATCH_SQL = """
    SELECT e.id
    FROM entities e
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND e.id > :last_id
    ORDER BY e.id
    LIMIT :limit
"""

_UPDATE_SQL = f"""
    UPDATE entities e
    SET unified_category_id = r.cat_id,
        unified_category = r.top_slug,
        unified_subcategory = r.leaf_slug
    FROM (
        SELECT e2.id AS entity_id,
               km.unified_category_id AS cat_id,
               top.slug AS top_slug,
               leaf.slug AS leaf_slug
        FROM entities e2
        {_RESOLVE_LATERAL}
        WHERE e2.source = :source
          AND e2.id = ANY(:ids)
    ) r
    WHERE e.id = r.entity_id
      AND (e.unified_category_id, e.unified_category, e.unified_subcategory)
          IS DISTINCT FROM (r.cat_id, r.top_slug, r.leaf_slug)
"""


async def _notify(progress_callback: Any, pct: float, message: str) -> None:
    if progress_callback is None:
        return
    try:
        outcome = progress_callback(pct, message)
    except TypeError:
        outcome = progress_callback(message)
    if inspect.isawaitable(outcome):
        await outcome


class UnifyOpentripmapCategories(AdminScript):
    meta = ScriptMeta(
        name="unify_opentripmap_categories",
        description=(
            "Populate unified_category_id, unified_category, and unified_subcategory"
            " for opentripmap entities by resolving secondary_types against"
            " place_kind_mappings (highest priority kind wins, deterministic"
            " tie-break). Falls back to the entity place_type as a candidate token."
        ),
        category="Unify",
        parameters=[
            ScriptParameter(
                name="source",
                type="select",
                label="Source",
                options=["opentripmap"],
                default="opentripmap",
                description="Source to unify (kind mappings required in place_kind_mappings)",
            ),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=500),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source") or "opentripmap"
        dry_run = bool(params.get("dry_run", True))
        batch_size = int(params.get("batch_size", 500) or 500)

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlmodel.ext.asyncio.session import AsyncSession

        from dmo.config import settings

        write_engine = create_async_engine(
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
        write_session = AsyncSession(write_engine)

        try:
            if dry_run:
                would_update = (
                    await write_session.execute(text(_DRY_RUN_COUNT_SQL), {"source": source})
                ).scalar() or 0
                unmapped_total = (
                    await write_session.execute(text(_UNMAPPED_COUNT_SQL), {"source": source})
                ).scalar() or 0
                unmapped_rows = (
                    await write_session.execute(text(_UNMAPPED_TOKENS_SQL), {"source": source})
                ).fetchall()
                details = [{"token": r[0], "count": r[1], "mapped": False} for r in unmapped_rows]
                msg = (
                    f"Would unify {would_update} {source} entities. "
                    f"{unmapped_total} unmapped entities, "
                    f"{len(unmapped_rows)} distinct unmapped tokens (top shown)."
                )
                return ScriptResult(
                    success=True,
                    message=msg,
                    affected_count=would_update,
                    details=details,
                )

            total_updated = 0
            total_scanned = 0
            last_id = "00000000-0000-0000-0000-000000000000"
            batch = 0

            while True:
                ids = (
                    (
                        await write_session.execute(
                            text(_SELECT_BATCH_SQL),
                            {"source": source, "last_id": last_id, "limit": batch_size},
                        )
                    )
                    .scalars()
                    .all()
                )
                if not ids:
                    break

                result = await write_session.execute(
                    text(_UPDATE_SQL), {"source": source, "ids": list(ids)}
                )
                total_updated += result.rowcount or 0
                total_scanned += len(ids)
                last_id = ids[-1]
                batch += 1
                await write_session.commit()
                await _notify(
                    progress_callback,
                    0.0,
                    f"Batch {batch}: scanned {total_scanned}, updated {total_updated}",
                )
        finally:
            await write_session.close()
            await write_engine.dispose()

        msg = f"Unified {total_updated} of {total_scanned} scanned {source} entities."
        return ScriptResult(success=True, message=msg, affected_count=total_updated)
