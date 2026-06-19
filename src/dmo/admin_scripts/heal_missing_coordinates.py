from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class HealMissingCoordinates(AdminScript):
    meta = ScriptMeta(
        name="heal_missing_coordinates",
        description="Find entities with missing lat/lon and attempt to fill from attributes JSONB or mark as inactive",
        category="Heal",
        parameters=[
            ScriptParameter(
                name="source",
                type="select",
                label="Source",
                options=["*"],
                default="*",
                description="Filter by source",
            ),
            ScriptParameter(
                name="dry_run",
                type="boolean",
                label="Dry Run",
                default=True,
                description="Preview changes without applying",
            ),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=100),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source", "*")
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 100))

        from sqlalchemy import text

        conditions = ["is_active = TRUE", "latitude IS NULL OR longitude IS NULL"]
        if source and source != "*":
            conditions.append("source = :source")

        where = " AND ".join(conditions)
        sql = text(
            f"SELECT id, source, source_id, name, attributes FROM entities WHERE {where} LIMIT :limit"
        )
        params_dict = {"limit": batch_size}
        if source and source != "*":
            params_dict["source"] = source

        result = await db.execute(sql, params_dict)
        rows = result.fetchall()

        if not rows:
            return ScriptResult(
                success=True, message="No entities with missing coordinates found", affected_count=0
            )

        details = []
        fixed = 0
        for i, row in enumerate(rows):
            entity_id = str(row[0])
            attrs = row[4] or {}
            lat = attrs.get("latitude") or attrs.get("lat")
            lon = attrs.get("longitude") or attrs.get("lon")

            if lat and lon:
                fixed += 1
                if not dry_run:
                    from sqlalchemy import text as sql_text

                    await db.execute(
                        sql_text(
                            "UPDATE entities SET latitude = :lat, longitude = :lon WHERE id = :id"
                        ),
                        {"lat": float(lat), "lon": float(lon), "id": row[0]},
                    )
                details.append(
                    {
                        "id": entity_id,
                        "name": row[3],
                        "source": row[1],
                        "source_id": row[2],
                        "action": "filled_from_attributes" if lat and lon else "still_missing",
                        "lat": float(lat) if lat else None,
                        "lon": float(lon) if lon else None,
                    }
                )
            else:
                details.append(
                    {
                        "id": entity_id,
                        "name": row[3],
                        "source": row[1],
                        "source_id": row[2],
                        "action": "no_coordinates_found",
                    }
                )

            if progress_callback:
                pct = ((i + 1) / len(rows)) * 100
                await progress_callback(pct, f"Processed {i + 1}/{len(rows)} entities")

        if not dry_run:
            await db.commit()

        action = "would_fix" if dry_run else "fixed"
        msg = f"{action} {fixed} entities with coordinates from attributes. {len(rows) - fixed} still missing."
        return ScriptResult(success=True, message=msg, affected_count=fixed, details=details)
