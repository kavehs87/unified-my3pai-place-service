from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult
from dmo.admin_scripts.helpers import create_script_engine, notify_progress

_SELECT_BATCH_SQL = """
    SELECT e.id
    FROM entities e
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND (e.thumbnail_url IS NULL OR e.thumbnail_url = '')
      AND EXISTS (
          SELECT 1 FROM media m
          WHERE m.entity_id = e.id AND m.is_active AND m.url <> ''
      )
      AND e.id > :last_id
    ORDER BY e.id
    LIMIT :limit
"""

_UPDATE_SQL = """
    UPDATE entities e
    SET thumbnail_url = m.url
    FROM (
        SELECT DISTINCT ON (entity_id) entity_id, url
        FROM media
        WHERE entity_id = ANY(:ids)
          AND is_active = TRUE
          AND url <> ''
        ORDER BY entity_id, sort_order NULLS LAST, id
    ) m
    WHERE e.id = m.entity_id
      AND (e.thumbnail_url IS NULL OR e.thumbnail_url = '')
"""

_DRY_RUN_SQL = """
    SELECT count(DISTINCT e.id)
    FROM entities e
    JOIN media m ON m.entity_id = e.id AND m.is_active AND m.url <> ''
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND (e.thumbnail_url IS NULL OR e.thumbnail_url = '')
"""

_SAMPLE_SQL = """
    SELECT left(e.name, 40), left(m.url, 70)
    FROM entities e
    JOIN media m ON m.entity_id = e.id AND m.is_active AND m.url <> ''
    WHERE e.is_active = TRUE
      AND e.source = :source
      AND (e.thumbnail_url IS NULL OR e.thumbnail_url = '')
    ORDER BY e.id
    LIMIT 5
"""


class BackfillThumbnailFromMedia(AdminScript):
    meta = ScriptMeta(
        name="backfill_thumbnail_from_media",
        description=(
            "Set entities.thumbnail_url from the first active media image"
            " (ordered by sort_order, id) where the entity has media but no thumbnail."
        ),
        category="Heal",
        parameters=[
            ScriptParameter(name="source", type="text", label="Source", default="swiss_dmo"),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=1000),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = (params.get("source") or "swiss_dmo").strip()
        dry_run = bool(params.get("dry_run", True))
        batch_size = int(params.get("batch_size", 1000) or 1000)

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
                    message=f"Would set thumbnail_url for {total} {source} entities.",
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
            message=f"Set thumbnail_url for {total_updated} {source} entities.",
            affected_count=total_updated,
        )
