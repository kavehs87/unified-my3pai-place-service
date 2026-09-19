import time

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult
from dmo.admin_scripts.country_data import ISO2_NAMES
from dmo.admin_scripts.helpers import notify_progress

_SELECT_BATCH_SQL = """
    SELECT id, locality, region, country
    FROM entities
    WHERE is_active = TRUE
      AND (address IS NULL OR address = '')
      AND (
          COALESCE(locality, '') <> '' OR
          COALESCE(region, '') <> '' OR
          COALESCE(country, '') <> ''
      )
      AND (:source = '*' OR source = :source)
      AND id > :last_id
    ORDER BY id
    LIMIT :limit
"""

_UPDATE_SQL = """
    UPDATE entities e
    SET address = m.addr
    FROM unnest(CAST(:ids AS uuid[]), CAST(:addrs AS text[])) AS m(id, addr)
    WHERE e.id = m.id
      AND (e.address IS NULL OR e.address = '')
"""


def build_address(locality, region, country) -> str | None:
    parts = []
    for value in (locality, region):
        if value and str(value).strip():
            parts.append(str(value).strip())
    if country and str(country).strip():
        code = str(country).strip()
        parts.append(ISO2_NAMES.get(code.upper(), code))
    return ", ".join(parts) if parts else None


class HealMissingAddress(AdminScript):
    meta = ScriptMeta(
        name="heal_missing_address",
        description=(
            "Synthesize entities.address from locality, region and country when"
            " address is empty and at least one component exists. ISO2 country"
            " codes are expanded to display names."
        ),
        category="Heal",
        parameters=[
            ScriptParameter(name="source", type="text", label="Source", default="*"),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=5000),
            ScriptParameter(
                name="max_seconds",
                type="int",
                label="Max Seconds",
                default=1200,
                description="Stop gracefully after this many seconds (0 = unlimited)",
            ),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = (params.get("source") or "*").strip() or "*"
        dry_run = bool(params.get("dry_run", True))
        batch_size = int(params.get("batch_size", 5000) or 5000)
        max_seconds = int(params.get("max_seconds", 1200) or 0)

        from sqlalchemy import text
        from sqlmodel.ext.asyncio.session import AsyncSession

        from dmo.admin_scripts.helpers import create_script_engine

        engine = create_script_engine()
        session = AsyncSession(engine)
        try:
            if dry_run:
                rows = (
                    await session.execute(
                        text(_SELECT_BATCH_SQL),
                        {
                            "source": source,
                            "last_id": "00000000-0000-0000-0000-000000000000",
                            "limit": 500,
                        },
                    )
                ).fetchall()
                total = (
                    await session.execute(
                        text(
                            """
                            SELECT count(*) FROM entities
                            WHERE is_active = TRUE
                              AND (address IS NULL OR address = '')
                              AND (COALESCE(locality,'') <> '' OR COALESCE(region,'') <> ''
                                   OR COALESCE(country,'') <> '')
                              AND (:source = '*' OR source = :source)
                            """
                        ),
                        {"source": source},
                    )
                ).scalar() or 0
                details = [
                    {
                        "name": None,
                        "address_preview": build_address(r[1], r[2], r[3]),
                    }
                    for r in rows[:5]
                ]
                return ScriptResult(
                    success=True,
                    message=f"Would synthesize address for {total} entities (source={source}).",
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
                rows = (
                    await session.execute(
                        text(_SELECT_BATCH_SQL),
                        {"source": source, "last_id": last_id, "limit": batch_size},
                    )
                ).fetchall()
                if not rows:
                    break
                ids = [r[0] for r in rows]
                addrs = [build_address(r[1], r[2], r[3]) for r in rows]
                keep = [(i, a) for i, a in zip(ids, addrs) if a]
                if keep:
                    result = await session.execute(
                        text(_UPDATE_SQL),
                        {"ids": [i for i, _ in keep], "addrs": [a for _, a in keep]},
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
            message=f"Synthesized address for {total_updated} entities (source={source}).",
            affected_count=total_updated,
        )
