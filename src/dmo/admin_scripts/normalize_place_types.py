from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class NormalizePlaceTypes(AdminScript):
    meta = ScriptMeta(
        name="normalize_place_types",
        description="Find and normalize inconsistent place_type values (e.g. 'Hotel' -> 'hotel', 'Restaurant ' -> 'restaurant')",
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
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source", "*")
        dry_run = params.get("dry_run", True)

        from sqlalchemy import text

        conditions = ["is_active = TRUE"]
        if source and source != "*":
            conditions.append("source = :source")

        where = " AND ".join(conditions)
        sql = text(f"SELECT DISTINCT place_type FROM entities WHERE {where} ORDER BY place_type")
        params_dict: dict = {}
        if source and source != "*":
            params_dict["source"] = source

        result = await db.execute(sql, params_dict)
        types = [r[0] for r in result.fetchall()]

        # Map of old -> normalized
        normalize_map = {}
        for pt in types:
            normalized = pt.strip().lower()
            if normalized != pt:
                normalize_map[pt] = normalized

        if not normalize_map:
            return ScriptResult(
                success=True, message="All place_types are already normalized", affected_count=0
            )

        details = []
        fixed = 0
        for old_type, new_type in normalize_map.items():
            count_sql = text(
                "SELECT COUNT(*) FROM entities WHERE place_type = :old AND is_active = TRUE"
            )
            cnt = (await db.execute(count_sql, {"old": old_type})).scalar() or 0

            fixed += cnt
            if not dry_run:
                await db.execute(
                    text(
                        "UPDATE entities SET place_type = :new WHERE place_type = :old AND is_active = TRUE"
                    ),
                    {"new": new_type, "old": old_type},
                )
            details.append(
                {
                    "old": old_type,
                    "new": new_type,
                    "count": cnt,
                }
            )

        # Also check secondary_types array
        if not dry_run:
            st_sql = text("""
                UPDATE entities
                SET secondary_types = ARRAY(
                    SELECT lower(trim(unnest(secondary_types)))
                )
                WHERE is_active = TRUE AND secondary_types IS NOT NULL
            """)
            await db.execute(st_sql)

        if not dry_run:
            await db.commit()

        action = "Would normalize" if dry_run else "Normalized"
        msg = f"{action} {fixed} entities across {len(normalize_map)} place_type variants"
        return ScriptResult(success=True, message=msg, affected_count=fixed, details=details)
