from sqlalchemy import text

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class FixWrappedWebsites(AdminScript):
    meta = ScriptMeta(
        name="fix_wrapped_websites",
        description='Fix website column values wrapped in JSON arrays: ["url"] -> url',
        category="Heal",
        parameters=[
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=1000),
        ],
    )

    async def run(self, params, db, llm=None, progress_callback=None):
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 1000))

        count_result = await db.execute(
            text("SELECT COUNT(*) FROM entities WHERE is_active = true AND website LIKE '[%]%")
        )
        total = count_result.scalar() or 0

        if total == 0:
            return ScriptResult(
                success=True,
                message="No wrapped websites found",
                affected_count=0,
            )

        offset = 0
        fixed = 0

        while offset < total:
            ids_result = await db.execute(
                text(
                    "SELECT id FROM entities "
                    "WHERE is_active = true AND website LIKE '[%]%' "
                    "LIMIT :bs OFFSET :off"
                ),
                {"bs": batch_size, "off": offset},
            )
            ids = [row[0] for row in ids_result.fetchall()]

            if not ids:
                break

            if not dry_run:
                await db.execute(
                    text(
                        "UPDATE entities SET website = ((website)::jsonb)->>0 WHERE id = ANY(:ids)"
                    ),
                    {"ids": ids},
                )

            fixed += len(ids)
            offset += len(ids)

            if progress_callback:
                pct = (offset / total) * 100
                await progress_callback(pct, f"Fixed {fixed}/{total}")

        if not dry_run:
            await db.commit()

        action = "Would fix" if dry_run else "Fixed"
        return ScriptResult(
            success=True,
            message=f"{action} website for {fixed} entities",
            affected_count=fixed,
        )
