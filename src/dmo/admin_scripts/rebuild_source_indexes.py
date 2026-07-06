"""Rebuild composite partial trigram indexes when enabled sources change.

When you enable/disable sources in the admin panel, the composite partial
indexes (idx_entities_name_trgm_enabled, idx_entities_summary_trgm_enabled)
become stale because their WHERE clause is hardcoded to the previously
enabled sources. This script drops and recreates them with the current
enabled source list.
"""

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class RebuildSourceIndexes(AdminScript):
    meta = ScriptMeta(
        name="rebuild_source_indexes",
        description="Rebuild composite partial trigram indexes to match current enabled sources. Run after enabling/disabling sources in the admin panel.",
        category="Maintenance",
        parameters=[
            ScriptParameter(
                name="dry_run",
                type="boolean",
                label="Dry Run",
                default=True,
                description="Preview changes without actually rebuilding indexes",
            ),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        dry_run = params.get("dry_run", True)

        from sqlalchemy import text

        # Get current enabled sources
        result = await db.execute(
            text("SELECT source FROM data_sources WHERE is_enabled = TRUE ORDER BY source")
        )
        enabled_sources = [row[0] for row in result.fetchall()]

        if not enabled_sources:
            return ScriptResult(
                success=True,
                message="No enabled sources found — nothing to index",
                affected_count=0,
            )

        # Build the IN clause for the partial index WHERE condition
        sources_in = ", ".join(f"'{s}'" for s in enabled_sources)

        # Check current indexes
        idx_result = await db.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'entities' AND indexname LIKE 'idx_entities_%_trgm_enabled'"
            )
        )
        current_indexes = {row[0]: row[1] for row in idx_result.fetchall()}

        # Build target index definitions
        target_indexes = {
            "idx_entities_name_trgm_enabled": (
                f"CREATE INDEX idx_entities_name_trgm_enabled "
                f"ON entities USING gin (name gin_trgm_ops) "
                f"WHERE (is_active = true AND source IN ({sources_in}))"
            ),
            "idx_entities_summary_trgm_enabled": (
                f"CREATE INDEX idx_entities_summary_trgm_enabled "
                f"ON entities USING gin (summary gin_trgm_ops) "
                f"WHERE (is_active = true AND source IN ({sources_in}))"
            ),
        }

        details = []
        dropped = []
        created = []

        for idx_name, target_def in target_indexes.items():
            needs_rebuild = False
            old_def = current_indexes.get(idx_name, "<does not exist>")

            if idx_name not in current_indexes or f"source IN ({sources_in})" not in old_def:
                needs_rebuild = True

            if needs_rebuild:
                details.append(
                    {
                        "index": idx_name,
                        "action": "rebuild",
                        "old": old_def,
                        "new": target_def,
                    }
                )
                dropped.append(idx_name)
                created.append(target_def)
            else:
                details.append(
                    {
                        "index": idx_name,
                        "action": "up_to_date",
                        "definition": old_def,
                    }
                )

        if not created:
            return ScriptResult(
                success=True,
                message=f"All indexes are up to date for sources: {enabled_sources}",
                affected_count=0,
                details=details,
            )

        if dry_run:
            return ScriptResult(
                success=True,
                message=f"Would rebuild {len(created)} index(es) for sources: {enabled_sources}",
                affected_count=len(created),
                details=details,
            )

        # Drop old indexes
        for idx_name in dropped:
            await db.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))

        # Create new indexes (CONCURRENTLY not available in non-transactional context,
        # but admin scripts run in a transaction, so we use regular CREATE)
        for create_sql in created:
            await db.execute(text(create_sql))

        await db.commit()

        return ScriptResult(
            success=True,
            message=f"Rebuilt {len(created)} index(es) for sources: {enabled_sources}",
            affected_count=len(created),
            details=details,
        )
