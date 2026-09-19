import time

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult
from dmo.admin_scripts.helpers import notify_progress

# Source-agnostic completeness score (0-100). Weights sum to exactly 100:
# text 23 · contacts/opening/price 21 · name 2 · visual 12 · location 18
# categorization 10 · engagement 8 · attribute richness 6
SCORE_EXPR = """
    (
        LEAST(COALESCE(length(e.description), 0) / 500.0, 1.0) * 15
        + LEAST(COALESCE(length(e.summary), 0) / 200.0, 1.0) * 8
        + CASE WHEN COALESCE(e.website, '') <> '' THEN 7 ELSE 0 END
        + CASE WHEN COALESCE(e.phone, '') <> '' THEN 4 ELSE 0 END
        + CASE WHEN COALESCE(e.email, '') <> '' THEN 2 ELSE 0 END
        + CASE WHEN COALESCE(e.opening_hours, '') <> '' THEN 3 ELSE 0 END
        + CASE WHEN e.is_open IS NOT NULL THEN 2 ELSE 0 END
        + CASE
            WHEN e.is_free = TRUE OR e.price_level IS NOT NULL OR e.price_min IS NOT NULL
            THEN 3 ELSE 0
          END
        + CASE
            WHEN COALESCE(e.name, '') <> '' AND e.name NOT LIKE 'Unnamed %'
            THEN 2 ELSE 0
          END
        + CASE WHEN COALESCE(e.thumbnail_url, '') <> '' THEN 8 ELSE 0 END
        + CASE
            WHEN EXISTS (SELECT 1 FROM media m WHERE m.entity_id = e.id AND m.is_active)
            THEN 4 ELSE 0
          END
        + CASE WHEN e.latitude IS NOT NULL AND e.longitude IS NOT NULL THEN 8 ELSE 0 END
        + CASE WHEN e.country ~ '^[A-Z]{2}$' THEN 3 ELSE 0 END
        + CASE WHEN COALESCE(e.region, '') <> '' THEN 3 ELSE 0 END
        + CASE WHEN COALESCE(e.locality, '') <> '' THEN 3 ELSE 0 END
        + CASE WHEN COALESCE(e.address, '') <> '' THEN 1 ELSE 0 END
        + CASE WHEN e.unified_category IS NOT NULL THEN 4 ELSE 0 END
        + CASE WHEN e.unified_subcategory IS NOT NULL THEN 3 ELSE 0 END
        + CASE
            WHEN e.secondary_types IS NOT NULL AND array_length(e.secondary_types, 1) > 0
            THEN 3 ELSE 0
          END
        + CASE WHEN e.rating IS NOT NULL THEN 2 ELSE 0 END
        + CASE WHEN COALESCE(e.reviews_count, 0) > 0 THEN 2 ELSE 0 END
        + CASE WHEN COALESCE(e.favorite_count, 0) > 0 THEN 2 ELSE 0 END
        + CASE WHEN e.is_featured THEN 2 ELSE 0 END
        + CASE WHEN ak.kc >= 3 THEN 3 ELSE 0 END
        + CASE WHEN ak.has_wiki THEN 3 ELSE 0 END
    )::int
"""

# Attribute keys are scanned once per row via LATERAL (kc = key count,
# has_wiki = any wikidata-style reference key).
_ATTRS_LATERAL = """
    LEFT JOIN LATERAL (
        SELECT count(*) AS kc,
               COALESCE(
                   bool_or(k = 'osm_wikidata' OR k = 'otm_wikidata' OR k LIKE :wiki_suffix),
                   FALSE
               ) AS has_wiki
        FROM jsonb_object_keys(COALESCE(e.attributes, '{}'::jsonb)) k
    ) ak ON TRUE
"""

_REPORT_SQL = f"""
    SELECT t.source,
           count(*) AS n,
           round(avg(score)::numeric, 1) AS avg_score,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY score) AS p50,
           percentile_cont(0.9) WITHIN GROUP (ORDER BY score) AS p90,
           min(score) AS min_score,
           max(score) AS max_score
    FROM (
        SELECT e.source, {SCORE_EXPR} AS score
        FROM entities e TABLESAMPLE SYSTEM (2)
        {_ATTRS_LATERAL}
        WHERE e.is_active
    ) t
    GROUP BY t.source
    ORDER BY n DESC
"""

_SELECT_BATCH_SQL = """
    SELECT id
    FROM entities
    WHERE is_active = TRUE
      AND (:source = '*' OR source = :source)
      AND id > :last_id
    ORDER BY id
    LIMIT :limit
"""

_UPDATE_SQL = f"""
    WITH batch AS (
        SELECT e.id, {SCORE_EXPR} AS score
        FROM entities e
        {_ATTRS_LATERAL}
        WHERE e.id = ANY(:ids)
    )
    UPDATE entities e
    SET quality_score = b.score
    FROM batch b
    WHERE e.id = b.id
      AND e.quality_score IS DISTINCT FROM b.score
"""


class ScoreEntities(AdminScript):
    meta = ScriptMeta(
        name="score_entities",
        description=(
            "Source-agnostic data-quality score (0-100) for all sources, stored in"
            " quality_score. Measures content completeness/exposure (description,"
            " summary, website, contacts, images, location, categorization,"
            " engagement, data richness). Supersedes score_osm_entities;"
            " idempotent (only rewrites changed scores). Dry-run uses a 2% page sample."
        ),
        category="Heal",
        parameters=[
            ScriptParameter(
                name="source",
                type="text",
                label="Source",
                default="*",
                description="Filter by source, or * for all",
            ),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=5000),
            ScriptParameter(
                name="max_seconds",
                type="int",
                label="Max Seconds",
                default=0,
                description="Stop gracefully after this many seconds (0 = unlimited)",
            ),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = (params.get("source") or "*").strip() or "*"
        dry_run = bool(params.get("dry_run", True))
        batch_size = int(params.get("batch_size", 5000) or 5000)
        max_seconds = int(params.get("max_seconds", 0) or 0)
        wiki_suffix = "%:wikidata"

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
                "server_settings": {"statement_timeout": "900000"},
                "prepared_statement_cache_size": 0,
            },
        )
        session = AsyncSession(engine)
        try:
            if dry_run:
                rows = (
                    await session.execute(text(_REPORT_SQL), {"wiki_suffix": wiki_suffix})
                ).fetchall()
                details = [
                    {
                        "source": r[0],
                        "sampled": r[1],
                        "avg": float(r[2]) if r[2] is not None else None,
                        "p50": float(r[3]) if r[3] is not None else None,
                        "p90": float(r[4]) if r[4] is not None else None,
                        "min": r[5],
                        "max": r[6],
                    }
                    for r in rows
                ]
                summary = "; ".join(
                    f"{d['source']}: avg {d['avg']} p50 {d['p50']} p90 {d['p90']}" for d in details
                )
                return ScriptResult(
                    success=True,
                    message=f"Score distribution (2% sample) — {summary}",
                    affected_count=sum(d["sampled"] for d in details),
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
                result = await session.execute(
                    text(_UPDATE_SQL),
                    {"ids": list(ids), "wiki_suffix": wiki_suffix},
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
            message=f"Scores updated for {total_updated} entities (source={source}).",
            affected_count=total_updated,
        )
