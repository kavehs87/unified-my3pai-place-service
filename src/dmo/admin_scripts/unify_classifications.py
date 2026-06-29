from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class UnifyClassifications(AdminScript):
    meta = ScriptMeta(
        name="unify_classifications",
        description=(
            "Backfill classifications table from source-specific attribute data"
            " (rexby_secondary_categories, tourpedia_services/features, OSM amenities)."
            " Uses batched keyset pagination with a dedicated engine for large datasets."
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

        source_clause = ""
        source_params: dict = {}
        if source and source != "*":
            source_clause = "AND e.source = :source"
            source_params["source"] = source

        results = {}

        if dry_run:
            if source in ("*", "rexby"):
                results["rexby_secondary"] = await self._count_json_array(
                    db,
                    "rexby",
                    "rexby_secondary_categories",
                    source_params,
                    source_clause,
                )
            if source in ("*", "tourpedia"):
                results["tourpedia_services"] = await self._count_json_array(
                    db,
                    "tourpedia",
                    "tourpedia_services",
                    source_params,
                    source_clause,
                )
                results["tourpedia_features"] = await self._count_json_array(
                    db,
                    "tourpedia",
                    "tourpedia_features",
                    source_params,
                    source_clause,
                )
            if source in ("*", "osm"):
                results["osm_cuisine"] = await self._count_entity_attribute(
                    db,
                    "osm",
                    "osm_cuisine",
                    source_params,
                    source_clause,
                )
        else:
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
                if source in ("*", "rexby"):
                    results["rexby_secondary"] = await self._backfill_json_array(
                        write_session,
                        batch_size,
                        "rexby",
                        "rexby_secondary_categories",
                        "secondary_category",
                        source_params,
                        source_clause,
                        progress_callback,
                        "Step 1/4 — Rexby secondary categories",
                    )

                if source in ("*", "tourpedia"):
                    results["tourpedia_services"] = await self._backfill_json_array(
                        write_session,
                        batch_size,
                        "tourpedia",
                        "tourpedia_services",
                        "service",
                        source_params,
                        source_clause,
                        progress_callback,
                        "Step 2/4 — Tourpedia services",
                    )
                    results["tourpedia_features"] = await self._backfill_json_array(
                        write_session,
                        batch_size,
                        "tourpedia",
                        "tourpedia_features",
                        "feature",
                        source_params,
                        source_clause,
                        progress_callback,
                        "Step 3/4 — Tourpedia features",
                    )

                if source in ("*", "osm"):
                    results["osm_cuisine"] = await self._backfill_osm_cuisine(
                        write_session,
                        batch_size,
                        source_params,
                        source_clause,
                        progress_callback,
                        "Step 4/4 — OSM cuisine",
                    )
            finally:
                await write_session.close()
                await write_engine.dispose()

        total = sum(results.values())
        action = "Would create" if dry_run else "Created"
        details = [{"step": k, "count": v} for k, v in results.items() if v > 0]
        msg = f"{action} {total} classifications across {len(details)} steps"

        return ScriptResult(success=True, message=msg, affected_count=total, details=details)

    async def _count_json_array(
        self,
        db,
        source_name,
        json_key,
        source_params,
        source_clause,
    ):
        from sqlalchemy import text

        clause = source_clause if source_params.get("source") == source_name else ""
        sql = text(f"""
            SELECT COUNT(*) FROM entities e
            WHERE e.source = :sn
              AND e.is_active = TRUE
              AND e.attributes ? :jk
              AND jsonb_typeof(e.attributes->:jk) = 'array'
              {clause}
        """)
        params = {"sn": source_name, "jk": json_key}
        if source_params.get("source") == source_name:
            params["source"] = source_params["source"]
        return (await db.execute(sql, params)).scalar() or 0

    async def _count_entity_attribute(
        self,
        db,
        source_name,
        json_key,
        source_params,
        source_clause,
    ):
        from sqlalchemy import text

        clause = source_clause if source_params.get("source") == source_name else ""
        sql = text(f"""
            SELECT COUNT(*) FROM entities e
            WHERE e.source = :sn
              AND e.is_active = TRUE
              AND e.attributes ? :jk
              AND NULLIF(e.attributes->>:jk, '') IS NOT NULL
              {clause}
        """)
        params = {"sn": source_name, "jk": json_key}
        if source_params.get("source") == source_name:
            params["source"] = source_params["source"]
        return (await db.execute(sql, params)).scalar() or 0

    async def _backfill_json_array(
        self,
        write_session,
        batch_size,
        source_name,
        json_key,
        category,
        source_params,
        source_clause,
        progress_callback,
        step_label,
    ):
        from sqlalchemy import text

        clause = source_clause if source_params.get("source") == source_name else ""

        select_sql = text(f"""
            SELECT e.id FROM entities e
            WHERE e.source = :sn
              AND e.is_active = TRUE
              AND e.attributes ? :jk
              AND jsonb_typeof(e.attributes->:jk) = 'array'
              {clause}
              AND e.id > :last_id
            ORDER BY e.id
            LIMIT :limit
        """)

        insert_sql = text("""
            WITH expanded AS (
                SELECT e.id, jsonb_array_elements_text(e.attributes->:jk) AS val
                FROM entities e
                WHERE e.id = ANY(:ids)
                  AND e.attributes ? :jk
                  AND jsonb_typeof(e.attributes->:jk) = 'array'
            )
            INSERT INTO classifications (entity_id, category, value_code, value_title)
            SELECT ex.id, :cat_val, ex.val, ex.val
            FROM expanded ex
            WHERE NOT EXISTS (
                SELECT 1 FROM classifications c
                WHERE c.entity_id = ex.id
                  AND c.category = :cat_col
                  AND c.value_code = ex.val
                  AND c.is_active = TRUE
            )
            ON CONFLICT (entity_id, category, value_code) WHERE is_active = TRUE
            DO NOTHING
        """)

        select_params = {
            "sn": source_name,
            "jk": json_key,
            "last_id": "00000000-0000-0000-0000-000000000000",
            "limit": batch_size,
        }
        if source_params.get("source") == source_name:
            select_params["source"] = source_params["source"]

        total = 0
        batch_num = 0
        while True:
            rows = (await write_session.execute(select_sql, select_params)).scalars().all()

            if not rows:
                break

            ids = list(rows)
            result = await write_session.execute(
                insert_sql,
                {"ids": ids, "jk": json_key, "cat_val": category, "cat_col": category},
            )
            total += result.rowcount
            await write_session.commit()

            select_params["last_id"] = rows[-1]
            batch_num += 1

            if progress_callback:
                progress_callback(
                    f"{step_label}: batch {batch_num}, "
                    f"{len(rows)} entities (total classifications: {total})"
                )

        return total

    async def _backfill_osm_cuisine(
        self,
        write_session,
        batch_size,
        source_params,
        source_clause,
        progress_callback,
        step_label,
    ):
        from sqlalchemy import text

        clause = source_clause if source_params.get("source") == "osm" else ""

        select_sql = text(f"""
            SELECT e.id FROM entities e
            WHERE e.source = 'osm'
              AND e.is_active = TRUE
              AND e.attributes ? 'osm_cuisine'
              AND NULLIF(e.attributes->>'osm_cuisine', '') IS NOT NULL
              {clause}
              AND e.id > :last_id
            ORDER BY e.id
            LIMIT :limit
        """)

        insert_sql = text("""
            WITH parsed AS (
                SELECT e.id, lower(trim(
                    unnest(string_to_array(e.attributes->>'osm_cuisine', ';'))
                )) AS cuisine
                FROM entities e
                WHERE e.id = ANY(:ids)
                  AND e.attributes ? 'osm_cuisine'
                  AND NULLIF(e.attributes->>'osm_cuisine', '') IS NOT NULL
            )
            INSERT INTO classifications (entity_id, category, value_code, value_title)
            SELECT p.id, 'amenity', p.cuisine, p.cuisine
            FROM parsed p
            WHERE NOT EXISTS (
                SELECT 1 FROM classifications c
                WHERE c.entity_id = p.id
                  AND c.category = 'amenity'
                  AND c.value_code = p.cuisine
                  AND c.is_active = TRUE
            )
            ON CONFLICT (entity_id, category, value_code) WHERE is_active = TRUE
            DO NOTHING
        """)

        select_params = {
            "last_id": "00000000-0000-0000-0000-000000000000",
            "limit": batch_size,
        }
        if source_params.get("source") == "osm":
            select_params["source"] = source_params["source"]

        total = 0
        batch_num = 0
        while True:
            rows = (await write_session.execute(select_sql, select_params)).scalars().all()

            if not rows:
                break

            ids = list(rows)
            result = await write_session.execute(
                insert_sql,
                {"ids": ids},
            )
            total += result.rowcount
            await write_session.commit()

            select_params["last_id"] = rows[-1]
            batch_num += 1

            if progress_callback:
                progress_callback(
                    f"{step_label}: batch {batch_num}, "
                    f"{len(rows)} entities (total classifications: {total})"
                )

        return total
