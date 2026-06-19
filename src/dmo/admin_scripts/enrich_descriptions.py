from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class EnrichDescriptions(AdminScript):
    meta = ScriptMeta(
        name="enrich_descriptions",
        description="Rephrase or enrich entity descriptions using the configured LLM endpoint",
        category="Enrich",
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
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=10),
            ScriptParameter(
                name="prompt_template",
                type="text",
                label="Prompt Template",
                default="Rephrase the following tourism description to be more engaging and professional. "
                "Keep it concise (max 3 paragraphs). Return only the rephrased text:\n\n{description}",
                description="Use {description} as placeholder for the original text",
            ),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source", "*")
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 10))
        prompt_template = params.get("prompt_template", "")

        if not llm:
            return ScriptResult(
                success=False,
                message="LLM not configured - set up in Settings first",
                affected_count=0,
            )

        if not prompt_template:
            return ScriptResult(
                success=False, message="Prompt template is required", affected_count=0
            )

        from sqlalchemy import text

        conditions = ["is_active = TRUE", "description IS NOT NULL", "description != ''"]
        if source and source != "*":
            conditions.append("source = :source")

        where = " AND ".join(conditions)
        sql = text(
            f"SELECT id, source, source_id, name, description FROM entities WHERE {where} LIMIT :limit"
        )
        params_dict = {"limit": batch_size}
        if source and source != "*":
            params_dict["source"] = source

        result = await db.execute(sql, params_dict)
        rows = result.fetchall()

        if not rows:
            return ScriptResult(
                success=True, message="No entities with descriptions found", affected_count=0
            )

        details = []
        enriched = 0
        for i, row in enumerate(rows):
            entity_id = str(row[0])
            original = row[4]

            prompt = prompt_template.replace("{description}", original)

            try:
                new_desc = await llm.chat(
                    [
                        {"role": "system", "content": "You are a professional tourism copywriter."},
                        {"role": "user", "content": prompt},
                    ]
                )
            except Exception as e:
                details.append(
                    {
                        "id": entity_id,
                        "name": row[3],
                        "source": row[1],
                        "error": str(e),
                    }
                )
                continue

            if new_desc and new_desc.strip():
                enriched += 1
                if not dry_run:
                    from sqlalchemy import text as sql_text

                    await db.execute(
                        sql_text(
                            "UPDATE entities SET description = :desc, description_format = 'text' WHERE id = :id"
                        ),
                        {"desc": new_desc.strip(), "id": row[0]},
                    )
                details.append(
                    {
                        "id": entity_id,
                        "name": row[3],
                        "source": row[1],
                        "original_length": len(original),
                        "new_length": len(new_desc),
                    }
                )

            if progress_callback:
                pct = ((i + 1) / len(rows)) * 100
                await progress_callback(pct, f"Enriched {i + 1}/{len(rows)}")

        if not dry_run:
            await db.commit()

        action = "Would enrich" if dry_run else "Enriched"
        msg = f"{action} {enriched} descriptions using LLM"
        return ScriptResult(success=True, message=msg, affected_count=enriched, details=details)
