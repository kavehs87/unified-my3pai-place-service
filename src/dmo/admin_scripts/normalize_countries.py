import time

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult
from dmo.admin_scripts.country_data import resolve_country
from dmo.admin_scripts.helpers import notify_progress

_DISTINCT_SQL = """
    SELECT country, count(*) AS cnt
    FROM entities
    WHERE is_active = TRUE
      AND country IS NOT NULL
      AND country <> ''
      AND country !~ '^[A-Z]{2}$'
      AND (:source = '*' OR source = :source)
    GROUP BY 1
    ORDER BY 2 DESC
"""

_CREATE_TEMP_SQL = """
    CREATE TEMP TABLE _country_map (
        raw  TEXT PRIMARY KEY,
        iso2 TEXT
    ) ON COMMIT PRESERVE ROWS
"""

_DROP_TEMP_SQL = "DROP TABLE IF EXISTS _country_map"

_INSERT_MAP_SQL = "INSERT INTO _country_map (raw, iso2) VALUES (:raw, :iso2)"

_SELECT_BATCH_SQL = """
    SELECT e.id
    FROM entities e
    JOIN _country_map m ON m.raw = e.country
    WHERE e.is_active = TRUE
      AND (:source = '*' OR e.source = :source)
      AND e.id > :last_id
    ORDER BY e.id
    LIMIT :limit
"""

_UPDATE_SQL = """
    UPDATE entities e
    SET country = m.iso2
    FROM _country_map m
    WHERE e.id = ANY(:ids)
      AND e.country = m.raw
"""


class NormalizeCountries(AdminScript):
    meta = ScriptMeta(
        name="normalize_countries",
        description=(
            "Normalize entities.country to ISO 3166-1 alpha-2 in row batches."
            " Known name variants (incl. native names and 'Name (Native)' patterns)"
            " are resolved; unmatched junk values are set to NULL. Use max_seconds"
            " to bound each run; rerun until the report shows 0 remaining."
        ),
        category="Normalize",
        parameters=[
            ScriptParameter(
                name="source",
                type="text",
                label="Source",
                default="*",
                description="Filter by source, or * for all",
            ),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Rows Per Batch", default=20000),
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
        batch_size = int(params.get("batch_size", 20000) or 20000)
        max_seconds = int(params.get("max_seconds", 1200) or 0)

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlmodel.ext.asyncio.session import AsyncSession

        from dmo.config import settings

        engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=2,
            max_overflow=0,
            pool_pre_ping=True,
            isolation_level="READ_COMMITTED",
            connect_args={
                "server_settings": {
                    "statement_timeout": "120000",
                    "synchronous_commit": "off",
                },
                "prepared_statement_cache_size": 0,
            },
        )
        session = AsyncSession(engine)
        try:
            rows = (await session.execute(text(_DISTINCT_SQL), {"source": source})).fetchall()
            pairs: list[tuple[str, str | None]] = []
            unresolved: list[tuple[str, int]] = []
            resolved_rows = 0
            nulled_rows = 0
            for raw, cnt in rows:
                iso2 = resolve_country(raw)
                pairs.append((raw, iso2))
                if iso2 is None:
                    unresolved.append((raw, cnt))
                    nulled_rows += cnt
                else:
                    resolved_rows += cnt

            total_rows = resolved_rows + nulled_rows
            details = [
                {"raw": raw[:80], "count": cnt, "action": "set_null"}
                for raw, cnt in unresolved[:20]
            ]
            if dry_run:
                return ScriptResult(
                    success=True,
                    message=(
                        f"[source={source}] {len(rows)} distinct non-ISO values covering"
                        f" {total_rows} rows: {resolved_rows} -> ISO2, {nulled_rows} -> NULL."
                    ),
                    affected_count=total_rows,
                    details=details,
                )

            if not pairs:
                return ScriptResult(
                    success=True,
                    message=f"[source={source}] Nothing to normalize (0 non-ISO values).",
                    affected_count=0,
                )

            await session.execute(text(_DROP_TEMP_SQL))
            await session.execute(text(_CREATE_TEMP_SQL))
            await session.execute(
                text(_INSERT_MAP_SQL),
                [{"raw": raw, "iso2": iso2} for raw, iso2 in pairs],
            )
            await session.commit()

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
                            {
                                "source": source,
                                "last_id": last_id,
                                "limit": batch_size,
                            },
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
                    progress_callback,
                    0.0,
                    f"Batch {batch}: updated {total_updated}",
                )
            await session.execute(text(_DROP_TEMP_SQL))
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

        remaining = nulled_rows + resolved_rows - total_updated
        message = (
            f"[source={source}] Normalized {total_updated} rows"
            f" ({len(pairs)} distinct values). Remaining in this run's scope:"
            f" ~{max(remaining, 0)} — rerun to continue."
        )
        return ScriptResult(
            success=True,
            message=message,
            affected_count=total_updated,
            details=details,
        )
