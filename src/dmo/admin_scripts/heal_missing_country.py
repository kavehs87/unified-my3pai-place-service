from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class HealMissingCountry(AdminScript):
    meta = ScriptMeta(
        name="heal_missing_country",
        description="Find entities with missing country and attempt to infer from region_names, locality, or attributes",
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
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=200),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source", "*")
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 200))

        from sqlalchemy import text

        conditions = ["is_active = TRUE", "(country IS NULL OR country = '')"]
        if source and source != "*":
            conditions.append("source = :source")

        where = " AND ".join(conditions)
        sql = text(
            f"SELECT id, source, source_id, name, region_names, locality, attributes FROM entities WHERE {where} LIMIT :limit"
        )
        params_dict = {"limit": batch_size}
        if source and source != "*":
            params_dict["source"] = source

        result = await db.execute(sql, params_dict)
        rows = result.fetchall()

        if not rows:
            return ScriptResult(
                success=True, message="No entities with missing country found", affected_count=0
            )

        # Common country name mappings for region inference
        country_map = {
            "switzerland": "CH",
            "suisse": "CH",
            "schweiz": "CH",
            "svizzera": "CH",
            "france": "FR",
            "germany": "DE",
            "deutschland": "DE",
            "italy": "IT",
            "italia": "IT",
            "austria": "AT",
            "österreich": "AT",
        }

        details = []
        fixed = 0
        for i, row in enumerate(rows):
            entity_id = str(row[0])
            region_names = row[4] or []
            locality = row[5] or ""
            attrs = row[6] or {}

            inferred = None

            # Check region_names array
            for rn in region_names if isinstance(region_names, list) else []:
                rn_lower = rn.lower().strip()
                if rn_lower in country_map:
                    inferred = country_map[rn_lower]
                    break

            # Check locality for country hints
            if not inferred and locality:
                for name, code in country_map.items():
                    if name in locality.lower():
                        inferred = code
                        break

            # Check attributes
            if not inferred:
                for key in ("country", "country_code", "countryCode", "country_name"):
                    val = attrs.get(key)
                    if val:
                        val_lower = str(val).lower().strip()
                        if val_lower in country_map:
                            inferred = country_map[val_lower]
                        elif len(str(val)) == 2:
                            inferred = str(val).upper()
                        else:
                            inferred = str(val).upper()[:2]
                        break

            if inferred:
                fixed += 1
                if not dry_run:
                    from sqlalchemy import text as sql_text

                    await db.execute(
                        sql_text("UPDATE entities SET country = :country WHERE id = :id"),
                        {"country": inferred, "id": row[0]},
                    )
                details.append(
                    {
                        "id": entity_id,
                        "name": row[3],
                        "source": row[1],
                        "inferred_country": inferred,
                        "from": "region_names"
                        if inferred
                        and any(
                            rn.lower().strip() in [k for k in country_map]
                            for rn in (region_names if isinstance(region_names, list) else [])
                        )
                        else ("locality" if locality else "attributes"),
                    }
                )

            if progress_callback:
                pct = ((i + 1) / len(rows)) * 100
                await progress_callback(pct, f"Processed {i + 1}/{len(rows)}")

        if not dry_run:
            await db.commit()

        action = "Would fill" if dry_run else "Filled"
        msg = f"{action} country for {fixed} entities. {len(rows) - fixed} still missing."
        return ScriptResult(success=True, message=msg, affected_count=fixed, details=details)
