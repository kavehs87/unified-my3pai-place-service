import time

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult
from dmo.admin_scripts.helpers import create_script_engine, notify_progress

_SKIP_KINDS = ("interesting_places", "other")

_SELECT_BATCH_SQL = """
    SELECT e.id
    FROM entities e
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND e.secondary_types IS NOT NULL
      AND array_length(e.secondary_types, 1) > 0
      AND e.id > :last_id
      AND EXISTS (
          SELECT 1
          FROM unnest(e.secondary_types) AS k
          WHERE k <> ALL(:skip_kinds)
            AND NOT EXISTS (
                SELECT 1 FROM classifications c
                WHERE c.entity_id = e.id
                  AND c.category = 'kind'
                  AND c.value_code = k
            )
      )
    ORDER BY e.id
    LIMIT :limit
"""

_INSERT_SQL = """
    INSERT INTO classifications (entity_id, category, value_code, value_title, is_active)
    SELECT e.id, 'kind', k, initcap(replace(k, '_', ' ')), TRUE
    FROM entities e
    CROSS JOIN LATERAL unnest(e.secondary_types) AS k
    WHERE e.id = ANY(:ids)
      AND k <> ALL(:skip_kinds)
    ON CONFLICT (entity_id, category, value_code) DO NOTHING
"""

_DRY_RUN_SQL = """
    SELECT count(*)
    FROM entities e
    CROSS JOIN LATERAL unnest(e.secondary_types) AS k
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND k <> ALL(:skip_kinds)
      AND NOT EXISTS (
          SELECT 1 FROM classifications c
          WHERE c.entity_id = e.id
            AND c.category = 'kind'
            AND c.value_code = k
      )
"""


class BackfillClassificationsFromKinds(AdminScript):
    meta = ScriptMeta(
        name="backfill_classifications_from_kinds",
        description=(
            "Create classification rows (category='kind') from entity secondary_types."
            " Skips generic tokens 'interesting_places' and 'other'."
            " Idempotent via ON CONFLICT DO NOTHING."
        ),
        category="Unify",
        parameters=[
            ScriptParameter(name="source", type="text", label="Source", default="opentripmap"),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(
                name="batch_size", type="int", label="Entities Per Batch", default=2000
            ),
            ScriptParameter(name="max_seconds", type="int", label="Max Seconds", default=1200),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = (params.get("source") or "opentripmap").strip()
        dry_run = bool(params.get("dry_run", True))
        batch_size = int(params.get("batch_size", 2000) or 2000)
        max_seconds = int(params.get("max_seconds", 1200) or 0)
        skip_kinds = list(_SKIP_KINDS)

        from sqlalchemy import text
        from sqlmodel.ext.asyncio.session import AsyncSession

        engine = create_script_engine()
        session = AsyncSession(engine)
        try:
            if dry_run:
                total = (
                    await session.execute(
                        text(_DRY_RUN_SQL),
                        {"source": source, "skip_kinds": skip_kinds},
                    )
                ).scalar() or 0
                return ScriptResult(
                    success=True,
                    message=(
                        f"Would insert {total} classification rows (category='kind') for {source}."
                    ),
                    affected_count=total,
                )

            started = time.perf_counter()
            total_inserted = 0
            batch = 0
            last_id = "00000000-0000-0000-0000-000000000000"
            while True:
                if max_seconds and time.perf_counter() - started > max_seconds:
                    break
                ids = (
                    (
                        await session.execute(
                            text(_SELECT_BATCH_SQL),
                            {
                                "source": source,
                                "last_id": last_id,
                                "limit": batch_size,
                                "skip_kinds": skip_kinds,
                            },
                        )
                    )
                    .scalars()
                    .all()
                )
                if not ids:
                    break
                result = await session.execute(
                    text(_INSERT_SQL), {"ids": list(ids), "skip_kinds": skip_kinds}
                )
                total_inserted += result.rowcount or 0
                last_id = ids[-1]
                batch += 1
                await session.commit()
                await notify_progress(
                    progress_callback, 0.0, f"Batch {batch}: inserted {total_inserted}"
                )
        finally:
            await session.close()
            await engine.dispose()

        return ScriptResult(
            success=True,
            message=f"Inserted {total_inserted} 'kind' classifications for {source}.",
            affected_count=total_inserted,
        )
