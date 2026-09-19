import time

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult
from dmo.admin_scripts.helpers import create_script_engine, notify_progress

_URL_EXPR = """
    COALESCE(
        attributes #>> '{tourpedia_external_links,google_maps,0}',
        attributes #>> '{tourpedia_external_links,foursquare,0}',
        attributes #>> '{tourpedia_external_links,google_plus,0}',
        attributes #>> '{tourpedia_external_links,facebook,0}',
        attributes #>> '{tourpedia_external_links,booking,0}',
        NULLIF(attributes->>'sm_url', '')
    )
"""

_DRY_RUN_SQL = f"""
    SELECT count(*)
    FROM entities
    WHERE is_active = TRUE
      AND source = :source
      AND (source_url IS NULL OR source_url = '')
      AND {_URL_EXPR} IS NOT NULL
"""

_SELECT_BATCH_SQL = f"""
    SELECT id
    FROM entities
    WHERE is_active = TRUE
      AND source = :source
      AND (source_url IS NULL OR source_url = '')
      AND {_URL_EXPR} IS NOT NULL
      AND id > :last_id
    ORDER BY id
    LIMIT :limit
"""

_UPDATE_SQL = f"""
    UPDATE entities
    SET source_url = {_URL_EXPR}
    WHERE id = ANY(:ids)
      AND (source_url IS NULL OR source_url = '')
"""

_SAMPLE_SQL = f"""
    SELECT left(name, 35), left({_URL_EXPR}, 80)
    FROM entities
    WHERE is_active = TRUE
      AND source = :source
      AND (source_url IS NULL OR source_url = '')
      AND {_URL_EXPR} IS NOT NULL
    LIMIT 5
"""


class BackfillSourceUrl(AdminScript):
    meta = ScriptMeta(
        name="backfill_source_url",
        description=(
            "Fill entities.source_url from source-specific attributes where empty."
            " Supports tourpedia (external links) and swiss_dmo (sm_url)."
        ),
        category="Heal",
        parameters=[
            ScriptParameter(name="source", type="text", label="Source", default="tourpedia"),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=10000),
            ScriptParameter(name="max_seconds", type="int", label="Max Seconds", default=1200),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = (params.get("source") or "tourpedia").strip()
        dry_run = bool(params.get("dry_run", True))
        batch_size = int(params.get("batch_size", 10000) or 10000)
        max_seconds = int(params.get("max_seconds", 1200) or 0)

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
                details = [{"name": r[0], "url": r[1]} for r in samples]
                return ScriptResult(
                    success=True,
                    message=f"Would set source_url for {total} {source} entities.",
                    affected_count=total,
                    details=details,
                )

            started = time.perf_counter()
            total_updated = 0
            batch = 0
            last_id = "00000000-0000-0000-0000-000000000000"
            while True:
                if max_seconds and time.perf_counter() - started > max_seconds:
                    break
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
                result = await session.execute(text(_UPDATE_SQL), {"ids": list(ids)})
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
            message=f"Set source_url for {total_updated} {source} entities.",
            affected_count=total_updated,
        )
