from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class UnifyPlaceTypes(AdminScript):
    meta = ScriptMeta(
        name="unify_place_types",
        description=(
            "Populate unified_category_id, unified_category, and unified_subcategory"
            " on entities from place_type_mappings. Run after normalize_place_types."
        ),
        category="Unify",
        parameters=[
            ScriptParameter(
                name="source",
                type="select",
                label="Source",
                options=["*"],
                default="*",
                description="Filter by source",
            ),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=500),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source", "*")
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 500))

        from sqlalchemy import text

        source_filter = ""
        source_params: dict = {}
        if source and source != "*":
            source_filter = "AND e.source = :source"
            source_params["source"] = source

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
                count_sql = text(f"""
                    SELECT COUNT(*) FROM entities e
                    JOIN place_type_mappings m
                      ON m.source = e.source AND m.source_place_type = e.place_type
                    WHERE e.is_active = TRUE
                      AND (e.unified_category IS DISTINCT FROM
                           (SELECT slug FROM unified_categories
                            WHERE id = m.unified_category_id))
                    {source_filter}
                """)
                total = (await write_session.execute(count_sql, source_params)).scalar() or 0
                unmapped_sql = text(f"""
                    SELECT e.place_type, COUNT(*) as cnt
                    FROM entities e
                    LEFT JOIN place_type_mappings m
                      ON m.source = e.source AND m.source_place_type = e.place_type
                    WHERE e.is_active = TRUE AND m.id IS NULL
                    {source_filter}
                    GROUP BY e.place_type
                    ORDER BY cnt DESC
                """)
                unmapped_rows = (
                    await write_session.execute(unmapped_sql, source_params)
                ).fetchall()
                details = [{"place_type": r[0], "count": r[1]} for r in unmapped_rows[:20]]
                msg = (
                    f"Would unify {total} entities. "
                    f"{len(unmapped_rows)} unmapped place_types remain."
                )
                return ScriptResult(
                    success=True, message=msg, affected_count=total, details=details
                )

            select_ids_sql = text(f"""
                SELECT e.id
                FROM entities e
                JOIN place_type_mappings m
                  ON m.source = e.source AND m.source_place_type = e.place_type
                WHERE e.is_active = TRUE
                  AND e.id > :last_id
                  {source_filter}
                ORDER BY e.id
                LIMIT :limit
            """)

            update_sql = text("""
                UPDATE entities e
                SET
                    unified_category_id = m.unified_category_id,
                    unified_category = p.slug,
                    unified_subcategory = l.slug
                FROM place_type_mappings m
                JOIN unified_categories l ON l.id = m.unified_category_id
                JOIN unified_categories p ON p.id = l.parent_id
                WHERE e.id = ANY(:ids)
                  AND e.source = m.source
                  AND e.place_type = m.source_place_type
            """)

            total_updated = 0
            last_id = "00000000-0000-0000-0000-000000000000"
            batch = 0

            while True:
                ids = (
                    (
                        await write_session.execute(
                            select_ids_sql,
                            {"last_id": last_id, "limit": batch_size, **source_params},
                        )
                    )
                    .scalars()
                    .all()
                )
                if not ids:
                    break
                await write_session.execute(update_sql, {"ids": list(ids)})
                total_updated += len(ids)
                last_id = ids[-1]
                batch += 1
                await write_session.commit()
                if progress_callback:
                    progress_callback(
                        f"Batch {batch}: updated {len(ids)} entities (total: {total_updated})"
                    )
        finally:
            await write_session.close()
            await write_engine.dispose()

        msg = f"Unified {total_updated} entities."
        return ScriptResult(success=True, message=msg, affected_count=total_updated)
