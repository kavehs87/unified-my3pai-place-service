from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult
from dmo.admin_scripts.helpers import create_script_engine, notify_progress

_SELECT_BATCH_SQL = """
    SELECT id
    FROM entities
    WHERE is_active = TRUE
      AND source = :source
      AND (summary IS NULL OR summary = '')
      AND description IS NOT NULL AND description <> ''
      AND id > :last_id
    ORDER BY id
    LIMIT :limit
"""

_UPDATE_SQL = """
    UPDATE entities
    SET summary = left(regexp_replace(description, '<[^>]+>', '', 'g'), :max_length)
    WHERE id = ANY(:ids)
      AND (summary IS NULL OR summary = '')
"""

_DRY_RUN_SQL = """
    SELECT count(*)
    FROM entities
    WHERE is_active = TRUE
      AND source = :source
      AND (summary IS NULL OR summary = '')
      AND description IS NOT NULL AND description <> ''
"""

_SAMPLE_SQL = """
    SELECT left(name, 40), left(description, 100)
    FROM entities
    WHERE is_active = TRUE AND source = :source
      AND (summary IS NULL OR summary = '')
      AND description IS NOT NULL AND description <> ''
    LIMIT 5
"""


class BackfillSummaryFromDescription(AdminScript):
    meta = ScriptMeta(
        name="backfill_summary_from_description",
        description=(
            "Fill entities.summary from entities.description where summary is empty."
            " Strips HTML tags and truncates to max_length."
        ),
        category="Heal",
        parameters=[
            ScriptParameter(name="source", type="text", label="Source", default="opentripmap"),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=1000),
            ScriptParameter(
                name="max_length",
                type="int",
                label="Max Summary Length",
                default=1000,
            ),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = (params.get("source") or "opentripmap").strip()
        dry_run = bool(params.get("dry_run", True))
        batch_size = int(params.get("batch_size", 1000) or 1000)
        max_length = int(params.get("max_length", 1000) or 1000)

        from sqlalchemy import text
        from sqlmodel.ext.asyncio.session import AsyncSession

        engine = create_script_engine()
        session = AsyncSession(engine)
        try:
            if dry_run:
                total = (
                    await session.execute(text(_DRY_RUN_SQL), {"source": source})
                ).scalar() or 0
                samples = (await session.execute(text(_SAMPLE_SQL), {"source": source})).fetchall()
                details = [{"name": r[0], "description": r[1]} for r in samples]
                return ScriptResult(
                    success=True,
                    message=f"Would backfill summary for {total} {source} entities.",
                    affected_count=total,
                    details=details,
                )

            total_updated = 0
            batch = 0
            last_id = "00000000-0000-0000-0000-000000000000"
            while True:
                ids = (
                    (
                        await session.execute(
                            text(_SELECT_BATCH_SQL),
                            {"source": source, "last_id": last_id, "limit": batch_size},
                        )
                    )
                    .scalars()
                    .all()
                )
                if not ids:
                    break
                result = await session.execute(
                    text(_UPDATE_SQL), {"ids": list(ids), "max_length": max_length}
                )
                total_updated += result.rowcount or 0
                last_id = ids[-1]
                batch += 1
                await session.commit()
                await notify_progress(
                    progress_callback, 0.0, f"Batch {batch}: updated {total_updated}"
                )
        finally:
            await session.close()
            await engine.dispose()

        return ScriptResult(
            success=True,
            message=f"Backfilled summary for {total_updated} {source} entities.",
            affected_count=total_updated,
        )
