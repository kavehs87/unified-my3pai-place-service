from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class FixDescriptionFormat(AdminScript):
    meta = ScriptMeta(
        name="fix_description_format",
        description="Detect and fix entities where description_format doesn't match the actual description content",
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
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=200),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        source = params.get("source", "*")
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 200))

        from sqlalchemy import text

        conditions = ["is_active = TRUE", "description IS NOT NULL"]
        if source and source != "*":
            conditions.append("source = :source")

        where = " AND ".join(conditions)
        sql = text(
            f"SELECT id, source, source_id, name, description, description_format FROM entities WHERE {where} LIMIT :limit"
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

        import re

        details = []
        fixed = 0
        for i, row in enumerate(rows):
            entity_id = str(row[0])
            desc = row[4] or ""
            current_format = row[5] or ""

            # Heuristic: detect HTML content
            is_html = bool(re.search(r"<[a-z][\s\S]*>", desc[:500]))
            is_prosemirror = desc.strip().startswith("{") and '"type"' in desc[:200]
            is_markdown = bool(re.search(r"^#{1,6}\s|\*\*|__|\[.*\]\(.*\)", desc[:500]))

            detected = (
                "html"
                if is_html
                else ("prosemirror" if is_prosemirror else ("markdown" if is_markdown else "text"))
            )

            if detected != current_format and current_format:
                fixed += 1
                if not dry_run:
                    from sqlalchemy import text as sql_text

                    await db.execute(
                        sql_text("UPDATE entities SET description_format = :fmt WHERE id = :id"),
                        {"fmt": detected, "id": row[0]},
                    )
                details.append(
                    {
                        "id": entity_id,
                        "name": row[3],
                        "source": row[1],
                        "action": f"changed '{current_format}' -> '{detected}'",
                    }
                )

            if progress_callback:
                pct = ((i + 1) / len(rows)) * 100
                await progress_callback(pct, f"Checked {i + 1}/{len(rows)}")

        if not dry_run:
            await db.commit()

        action = "Would fix" if dry_run else "Fixed"
        msg = f"{action} {fixed} entities with mismatched description_format"
        return ScriptResult(success=True, message=msg, affected_count=fixed, details=details)
