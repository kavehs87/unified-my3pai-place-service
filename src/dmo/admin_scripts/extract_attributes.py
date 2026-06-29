from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class ExtractAttributes(AdminScript):
    meta = ScriptMeta(
        name="extract_attributes",
        description=(
            "Extract website/thumbnail_url from source-specific attributes and"
            " normalize amenity key names inside attributes JSONB."
        ),
        category="Fix",
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
            ScriptParameter(
                name="force",
                type="boolean",
                label="Force Overwrite",
                default=False,
                description="Overwrite already-populated website/thumbnail_url",
            ),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=500),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source", "*")
        dry_run = params.get("dry_run", True)
        force = params.get("force", False)
        batch_size = int(params.get("batch_size", 500))

        from sqlalchemy import text

        source_clause = ""
        source_params: dict = {}
        if source and source != "*":
            source_clause = "AND e.source = :source"
            source_params["source"] = source

        skip_website = "" if force else "AND e.website IS NULL"
        skip_thumb = "" if force else "AND e.thumbnail_url IS NULL"

        results = {}

        async def _dry_count(count_sql, count_params):
            result = await db.execute(count_sql, count_params)
            return result.scalar() or 0

        if dry_run:
            tp_website_count = text(f"""
                SELECT COUNT(*) FROM entities e
                WHERE e.source = 'tourpedia'
                  AND e.is_active = TRUE
                  AND e.attributes ? 'tourpedia_external_links'
                  AND e.attributes->'tourpedia_external_links' != '{{}}'::jsonb
                  {skip_website}
                  {source_clause if source == "tourpedia" else ""}
            """)
            results["tourpedia_website"] = await _dry_count(
                tp_website_count, source_params if source == "tourpedia" else {}
            )

            tp_thumb_count = text(f"""
                SELECT COUNT(*) FROM entities e
                WHERE e.source = 'tourpedia'
                  AND e.is_active = TRUE
                  AND e.attributes ? 'tourpedia_photo_url'
                  AND NULLIF(e.attributes->>'tourpedia_photo_url', '') IS NOT NULL
                  {skip_thumb}
                  {source_clause if source == "tourpedia" else ""}
            """)
            results["tourpedia_thumbnail"] = await _dry_count(
                tp_thumb_count, source_params if source == "tourpedia" else {}
            )

            osm_website_count = text(f"""
                SELECT COUNT(*) FROM entities e
                WHERE e.source = 'osm'
                  AND e.is_active = TRUE
                  AND e.attributes::text ~ 'website'
                  {skip_website}
                  {source_clause if source == "osm" else ""}
            """)
            results["osm_website"] = await _dry_count(
                osm_website_count, source_params if source == "osm" else {}
            )

            amenity_count = text(f"""
                SELECT COUNT(*) FROM entities e
                WHERE e.is_active = TRUE
                  AND (
                    e.attributes ? 'osm_dine_in'
                    OR e.attributes ? 'osm_takeout'
                    OR e.attributes ? 'osm_delivery'
                    OR e.attributes ? 'osm_cuisine'
                  )
                  {source_clause}
            """)
            results["amenity_normalization"] = await _dry_count(amenity_count, source_params)

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
                # ── 1. Tourpedia: website from tourpedia_external_links ──
                tp_website_select = text(f"""
                    SELECT e.id FROM entities e
                    WHERE e.source = 'tourpedia'
                      AND e.is_active = TRUE
                      AND e.attributes ? 'tourpedia_external_links'
                      AND e.attributes->'tourpedia_external_links' != '{{}}'::jsonb
                      {skip_website}
                      {source_clause if source == "tourpedia" else ""}
                      AND e.id > :last_id
                    ORDER BY e.id
                    LIMIT :limit
                """)
                tp_website_update = text("""
                    UPDATE entities e
                    SET website = COALESCE(
                        NULLIF(e.attributes->'tourpedia_external_links'->>'facebook', ''),
                        NULLIF(e.attributes->'tourpedia_external_links'->>'foursquare', ''),
                        NULLIF(e.attributes->'tourpedia_external_links'->>'google_plus', '')
                    )
                    WHERE e.id = ANY(:ids)
                      AND COALESCE(
                        NULLIF(e.attributes->'tourpedia_external_links'->>'facebook', ''),
                        NULLIF(e.attributes->'tourpedia_external_links'->>'foursquare', ''),
                        NULLIF(e.attributes->'tourpedia_external_links'->>'google_plus', '')
                      ) IS NOT NULL
                    RETURNING e.id
                """)

                tp_website_total = 0
                last_id = "00000000-0000-0000-0000-000000000000"
                batch = 0
                while True:
                    rows = (
                        (
                            await write_session.execute(
                                tp_website_select,
                                {
                                    "last_id": last_id,
                                    "limit": batch_size,
                                    **(source_params if source == "tourpedia" else {}),
                                },
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if not rows:
                        break
                    result = await write_session.execute(tp_website_update, {"ids": list(rows)})
                    tp_website_total += len(result.scalars().all())
                    last_id = rows[-1]
                    batch += 1
                    await write_session.commit()
                    if progress_callback:
                        progress_callback(
                            f"Step 1/4 — Tourpedia website: batch {batch}, "
                            f"{len(rows)} rows (total: {tp_website_total})"
                        )
                results["tourpedia_website"] = tp_website_total

                # ── 2. Tourpedia: thumbnail from tourpedia_photo_url ──
                tp_thumb_select = text(f"""
                    SELECT e.id FROM entities e
                    WHERE e.source = 'tourpedia'
                      AND e.is_active = TRUE
                      AND e.attributes ? 'tourpedia_photo_url'
                      AND NULLIF(e.attributes->>'tourpedia_photo_url', '') IS NOT NULL
                      {skip_thumb}
                      {source_clause if source == "tourpedia" else ""}
                      AND e.id > :last_id
                    ORDER BY e.id
                    LIMIT :limit
                """)
                tp_thumb_update = text("""
                    UPDATE entities e
                    SET thumbnail_url = NULLIF(e.attributes->>'tourpedia_photo_url', '')
                    WHERE e.id = ANY(:ids)
                    RETURNING e.id
                """)

                tp_thumb_total = 0
                last_id = "00000000-0000-0000-0000-000000000000"
                batch = 0
                while True:
                    rows = (
                        (
                            await write_session.execute(
                                tp_thumb_select,
                                {
                                    "last_id": last_id,
                                    "limit": batch_size,
                                    **(source_params if source == "tourpedia" else {}),
                                },
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if not rows:
                        break
                    result = await write_session.execute(tp_thumb_update, {"ids": list(rows)})
                    tp_thumb_total += len(result.scalars().all())
                    last_id = rows[-1]
                    batch += 1
                    await write_session.commit()
                    if progress_callback:
                        progress_callback(
                            f"Step 2/4 — Tourpedia thumbnail: batch {batch}, "
                            f"{len(rows)} rows (total: {tp_thumb_total})"
                        )
                results["tourpedia_thumbnail"] = tp_thumb_total

                # ── 3. OSM: website from common osm_* keys ──
                osm_website_select = text(f"""
                    SELECT e.id FROM entities e
                    WHERE e.source = 'osm'
                      AND e.is_active = TRUE
                      AND e.attributes::text ~ 'website'
                      {skip_website}
                      {source_clause if source == "osm" else ""}
                      AND e.id > :last_id
                    ORDER BY e.id
                    LIMIT :limit
                """)
                osm_website_update = text("""
                    UPDATE entities e
                    SET website = COALESCE(
                        NULLIF(e.attributes->>'osm_contact:website', ''),
                        NULLIF(e.attributes->>'osm_website', ''),
                        NULLIF(e.attributes->>'osm_website:official', ''),
                        NULLIF(e.attributes->>'osm_website:en', ''),
                        NULLIF(e.attributes->>'osm_website:de', ''),
                        NULLIF(e.attributes->>'osm_website:fr', ''),
                        NULLIF(e.attributes->>'osm_website:it', ''),
                        NULLIF(e.attributes->>'osm_contact:website:de', ''),
                        NULLIF(e.attributes->>'osm_contact:website:en', ''),
                        NULLIF(e.attributes->>'osm_contact:website:fr', ''),
                        NULLIF(e.attributes->>'osm_website:main', ''),
                        NULLIF(e.attributes->>'osm_website:1', ''),
                        NULLIF(e.attributes->>'osm_website1', ''),
                        NULLIF(e.attributes->>'osm_website_1', ''),
                        NULLIF(e.attributes->>'osm_website_alt', ''),
                        NULLIF(e.attributes->>'osm_alt_website', ''),
                        NULLIF(e.attributes->>'osm_heritage:website', ''),
                        NULLIF(e.attributes->>'osm_operator:website', ''),
                        NULLIF(e.attributes->>'osm_brand:website', '')
                    )
                    WHERE e.id = ANY(:ids)
                      AND COALESCE(
                        NULLIF(e.attributes->>'osm_contact:website', ''),
                        NULLIF(e.attributes->>'osm_website', ''),
                        NULLIF(e.attributes->>'osm_website:official', ''),
                        NULLIF(e.attributes->>'osm_website:en', ''),
                        NULLIF(e.attributes->>'osm_website:de', ''),
                        NULLIF(e.attributes->>'osm_website:fr', ''),
                        NULLIF(e.attributes->>'osm_website:it', ''),
                        NULLIF(e.attributes->>'osm_contact:website:de', ''),
                        NULLIF(e.attributes->>'osm_contact:website:en', ''),
                        NULLIF(e.attributes->>'osm_contact:website:fr', ''),
                        NULLIF(e.attributes->>'osm_website:main', ''),
                        NULLIF(e.attributes->>'osm_website:1', ''),
                        NULLIF(e.attributes->>'osm_website1', ''),
                        NULLIF(e.attributes->>'osm_website_1', ''),
                        NULLIF(e.attributes->>'osm_website_alt', ''),
                        NULLIF(e.attributes->>'osm_alt_website', ''),
                        NULLIF(e.attributes->>'osm_heritage:website', ''),
                        NULLIF(e.attributes->>'osm_operator:website', ''),
                        NULLIF(e.attributes->>'osm_brand:website', '')
                      ) IS NOT NULL
                    RETURNING e.id
                """)

                osm_website_total = 0
                last_id = "00000000-0000-0000-0000-000000000000"
                batch = 0
                while True:
                    rows = (
                        (
                            await write_session.execute(
                                osm_website_select,
                                {
                                    "last_id": last_id,
                                    "limit": batch_size,
                                    **(source_params if source == "osm" else {}),
                                },
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if not rows:
                        break
                    result = await write_session.execute(osm_website_update, {"ids": list(rows)})
                    osm_website_total += len(result.scalars().all())
                    last_id = rows[-1]
                    batch += 1
                    await write_session.commit()
                    if progress_callback:
                        progress_callback(
                            f"Step 3/4 — OSM website: batch {batch}, "
                            f"{len(rows)} rows (total: {osm_website_total})"
                        )
                results["osm_website"] = osm_website_total

                # ── 4. Amenity normalization in attributes JSONB ──
                amenity_select = text(f"""
                    SELECT e.id FROM entities e
                    WHERE e.is_active = TRUE
                      AND (
                        e.attributes ? 'osm_dine_in'
                        OR e.attributes ? 'osm_takeout'
                        OR e.attributes ? 'osm_delivery'
                        OR e.attributes ? 'osm_cuisine'
                      )
                      {source_clause}
                      AND e.id > :last_id
                    ORDER BY e.id
                    LIMIT :limit
                """)
                amenity_update = text("""
                    UPDATE entities e
                    SET attributes = jsonb_set(
                        jsonb_set(
                            jsonb_set(
                                jsonb_set(
                                    e.attributes,
                                    '{dine_in}',
                                    COALESCE(e.attributes->'osm_dine_in', e.attributes->'osmx_dine_in', 'null'::jsonb),
                                    true
                                ),
                                '{takeout}',
                                COALESCE(e.attributes->'osm_takeout', e.attributes->'osmx_takeout', 'null'::jsonb),
                                true
                            ),
                            '{delivery}',
                            COALESCE(e.attributes->'osm_delivery', e.attributes->'osmx_delivery', 'null'::jsonb),
                            true
                        ),
                        '{cuisine_type}',
                        COALESCE(
                            e.attributes->'osm_cuisine',
                            e.attributes->'tourpedia_cuisine',
                            e.attributes->'rexby_cuisine',
                            'null'::jsonb
                        ),
                        true
                    )
                    WHERE e.id = ANY(:ids)
                    RETURNING e.id
                """)

                amenity_total = 0
                last_id = "00000000-0000-0000-0000-000000000000"
                batch = 0
                while True:
                    rows = (
                        (
                            await write_session.execute(
                                amenity_select,
                                {"last_id": last_id, "limit": batch_size, **source_params},
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if not rows:
                        break
                    result = await write_session.execute(amenity_update, {"ids": list(rows)})
                    amenity_total += len(result.scalars().all())
                    last_id = rows[-1]
                    batch += 1
                    await write_session.commit()
                    if progress_callback:
                        progress_callback(
                            f"Step 4/4 — Amenity normalization: batch {batch}, "
                            f"{len(rows)} rows (total: {amenity_total})"
                        )
                results["amenity_normalization"] = amenity_total

            finally:
                await write_session.close()
                await write_engine.dispose()

        total = sum(results.values())
        action = "Would extract" if dry_run else "Extracted"
        details = [{"step": k, "count": v} for k, v in results.items() if v > 0]
        msg = f"{action} data from {total} entities across {len([v for v in results.values() if v > 0])} steps"

        return ScriptResult(success=True, message=msg, affected_count=total, details=details)
