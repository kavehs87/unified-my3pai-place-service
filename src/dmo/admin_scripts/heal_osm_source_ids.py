from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class HealOsmSourceIds(AdminScript):
    meta = ScriptMeta(
        name="heal_osm_source_ids",
        description="Strip 'node/' prefix from OSM source_ids to fix API route collision with FastAPI path params",
        category="Heal",
        parameters=[
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        dry_run = params.get("dry_run", True)

        from sqlalchemy import text

        # ── Collision check ──
        collision_result = await db.execute(
            text(
                """
                SELECT e1.source_id, substring(e1.source_id FROM '/(.*)') as stripped
                FROM entities e1
                JOIN entities e2 ON e2.source = 'osm'
                                 AND e2.source_id = substring(e1.source_id FROM '/(.*)')
                                 AND e2.id <> e1.id
                WHERE e1.source = 'osm'
                  AND e1.source_id LIKE 'node/%'
                  AND e1.is_active = TRUE
                  AND e2.is_active = TRUE
                LIMIT 1
                """
            )
        )
        collision = collision_result.fetchone()
        if collision:
            return ScriptResult(
                success=False,
                message=f"UNIQUE COLLISION: 'node/{collision.stripped}' and '{collision.stripped}' both exist",
                affected_count=0,
                details=[
                    {
                        "collision_original": collision.source_id,
                        "collision_existing": collision.stripped,
                    }
                ],
            )

        # ── Count affected ──
        count_result = await db.execute(
            text(
                """
                SELECT count(*) FROM entities
                WHERE source = 'osm' AND source_id LIKE 'node/%' AND is_active = TRUE
                """
            )
        )
        affected = count_result.scalar() or 0

        if affected == 0:
            return ScriptResult(
                success=True, message="No OSM entities need healing", affected_count=0
            )

        if dry_run:
            return ScriptResult(
                success=True,
                message=f"Would strip 'node/' prefix from {affected} OSM entities (no collisions)",
                affected_count=affected,
                details=[{"sample_before": "node/1492591809", "sample_after": "1492591809"}],
            )

        # ── Execute heal ──
        await db.execute(
            text(
                """
                UPDATE entities
                SET source_id = substring(source_id FROM '/(.*)')
                WHERE source = 'osm'
                  AND source_id LIKE 'node/%'
                  AND is_active = TRUE
                """
            )
        )
        await db.commit()

        return ScriptResult(
            success=True,
            message=f"Stripped 'node/' prefix from {affected} OSM entities",
            affected_count=affected,
        )
