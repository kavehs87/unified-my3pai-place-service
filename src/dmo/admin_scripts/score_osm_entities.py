from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult


class ScoreOSMEntities(AdminScript):
    meta = ScriptMeta(
        name="score_osm_entities",
        description="Score OSM entities (0-100) based on data quality: wiki links, name, description, website, phone, address, opening hours, summary, thumbnail, rating, osm_ref, osm_heritage, secondary wiki keys, attribute richness. Updates quality_score column.",
        category="Heal",
        parameters=[
            ScriptParameter(
                name="source",
                type="select",
                label="Source",
                options=["osm"],
                default="osm",
                description="Data source to score (osm only)",
            ),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(name="batch_size", type="int", label="Batch Size", default=5000),
            ScriptParameter(
                name="action",
                type="select",
                label="Action",
                options=["score", "report", "cleanup"],
                default="score",
                description="score = compute and write scores, report = show distribution only, cleanup = soft-delete garbage tier",
            ),
            ScriptParameter(
                name="min_score",
                type="int",
                label="Min Score Threshold",
                default=15,
                description="For cleanup: soft-delete entities scoring below this value",
            ),
        ],
    )

    _WIKI_SUFFIXES = (":wikidata", ":wikipedia", ":wikimedia_commons")

    def _compute_score(self, row: tuple, attrs: dict) -> int:
        (
            entity_id,
            name,
            description,
            website,
            phone,
            address,
            opening_hours,
            summary,
            thumbnail_url,
            rating,
        ) = row

        score = 0

        # Primary wiki links
        wd = attrs.get("osm_wikidata", "")
        wp = attrs.get("osm_wikipedia", "")
        wc = attrs.get("osm_wikimedia_commons", "")
        ref = attrs.get("osm_ref", "")
        heritage = attrs.get("osm_heritage", "")

        if wd:
            score += 20
        if wp:
            score += 15
        if wc:
            score += 8
        if ref:
            score += 5
        if heritage:
            score += 2

        # Content fields
        if name and not name.startswith("Unnamed "):
            score += 12
        if description and description.strip():
            score += 10
        if website and website.strip():
            score += 7
        if phone and phone.strip():
            score += 4
        if address and address.strip():
            score += 4
        if opening_hours and opening_hours.strip():
            score += 3
        if summary and summary.strip():
            score += 3
        if thumbnail_url and thumbnail_url.strip():
            score += 2
        if rating is not None:
            score += 2

        # Secondary wiki keys (pattern match on attribute keys)
        secondary_count = sum(1 for k in attrs if any(k.endswith(s) for s in self._WIKI_SUFFIXES))
        score += min(9, secondary_count * 3)

        # Attribute richness (0.5 per key, max 10)
        score += min(10, len(attrs) * 0.5)

        # Negative: unnamed penalty
        if name and name.startswith("Unnamed "):
            score -= 10

        return max(0, min(100, round(score)))

    async def run(self, params, db, llm=None, progress_callback=None):
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
        from sqlalchemy.ext.asyncio import AsyncSession as SAAsyncSession

        from dmo.config import settings

        source = params.get("source", "osm")
        dry_run = params.get("dry_run", True)
        batch_size = int(params.get("batch_size", 5000))
        action = params.get("action", "score")
        min_score = int(params.get("min_score", 15))

        if source != "osm":
            return ScriptResult(
                success=False,
                message="This script only scores OSM entities. Set source=osm.",
                affected_count=0,
            )

        # For score/cleanup, use READ_COMMITTED engine to avoid serialization conflicts
        # with concurrent API writes (main pool uses REPEATABLE_READ)
        if action in ("score", "cleanup"):
            engine: AsyncEngine = create_async_engine(
                settings.database_url,
                isolation_level="READ_COMMITTED",
            )
            db = SAAsyncSession(engine)

        # Get total count
        total_result = await db.execute(
            text("SELECT COUNT(*) FROM entities WHERE source = :source AND is_active = TRUE"),
            {"source": source},
        )
        total = total_result.scalar() or 0

        if not total:
            await db.close()
            return ScriptResult(success=True, message="No OSM entities found", affected_count=0)

        if action == "report":
            return await self._report(db, source)

        if action == "cleanup":
            result = await self._cleanup(db, source, min_score, dry_run)
            await db.close()
            return result

        # Score action — keyset pagination for resumability, commit per batch
        scored = 0
        tier_counts = {"garbage": 0, "low": 0, "medium": 0, "good": 0, "excellent": 0}
        details = []
        last_id = None

        # Count already-scored entities to show real progress on resume
        scored_count_result = await db.execute(
            text(
                "SELECT COUNT(*) FROM entities "
                "WHERE source = :source AND is_active = TRUE AND quality_score IS NOT NULL"
            ),
            {"source": source},
        )
        already_scored = scored_count_result.scalar() or 0
        remaining = total - already_scored

        if already_scored:
            details.append(
                f"Resuming: {already_scored}/{total} already scored, {remaining} remaining"
            )

        # Find starting point: skip already-scored entities
        first_result = await db.execute(
            text(
                "SELECT id FROM entities "
                "WHERE source = :source AND is_active = TRUE AND quality_score IS NULL "
                "ORDER BY id LIMIT 1"
            ),
            {"source": source},
        )
        first_row = first_result.fetchone()
        if first_row:
            last_id = first_row[0]

        while last_id is not None:
            rows_result = await db.execute(
                text(
                    "SELECT id, name, description, website, phone, address, "
                    "opening_hours, summary, thumbnail_url, rating, attributes "
                    "FROM entities WHERE source = :source AND is_active = TRUE "
                    "AND quality_score IS NULL AND id >= :last_id "
                    "ORDER BY id LIMIT :limit"
                ),
                {"source": source, "last_id": last_id, "limit": batch_size},
            )
            rows = rows_result.fetchall()

            if not rows:
                break

            batch_scores = []
            for row in rows:
                attrs = row[10] or {}
                score = self._compute_score(row[:10], attrs)
                batch_scores.append((score, str(row[0])))

                if score <= 15:
                    tier_counts["garbage"] += 1
                elif score <= 35:
                    tier_counts["low"] += 1
                elif score <= 60:
                    tier_counts["medium"] += 1
                elif score <= 85:
                    tier_counts["good"] += 1
                else:
                    tier_counts["excellent"] += 1

            if not dry_run and batch_scores:
                for score, entity_id in batch_scores:
                    await db.execute(
                        text("UPDATE entities SET quality_score = :score WHERE id = :id"),
                        {"score": score, "id": entity_id},
                    )
                await db.commit()

            scored += len(rows)
            last_id = rows[-1][0]

            if progress_callback:
                total_scored = already_scored + scored
                pct = min(100, (total_scored / total) * 100)
                await progress_callback(pct, f"{total_scored}/{total} scored ({scored} this run)")

        await db.close()

        action_label = "Would score" if dry_run else "Scored"
        msg = f"{action_label} {scored} OSM entities."
        details.append({"tier_distribution": tier_counts})

        return ScriptResult(
            success=True,
            message=msg,
            affected_count=scored,
            details=details,
        )

    async def _report(self, db, source: str) -> ScriptResult:
        """Show current score distribution from quality_score column."""
        from sqlalchemy import text

        result = await db.execute(
            text(
                "SELECT "
                "COUNT(*) as total,"
                "COUNT(*) FILTER (WHERE quality_score IS NULL) as unscored,"
                "COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score <= 15) as garbage,"
                "COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score BETWEEN 16 AND 35) as low,"
                "COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score BETWEEN 36 AND 60) as medium,"
                "COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score BETWEEN 61 AND 85) as good,"
                "COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score > 85) as excellent,"
                "AVG(quality_score) FILTER (WHERE quality_score IS NOT NULL) as avg_score,"
                "MIN(quality_score) FILTER (WHERE quality_score IS NOT NULL) as min_score,"
                "MAX(quality_score) FILTER (WHERE quality_score IS NOT NULL) as max_score"
                " FROM entities WHERE source = :source AND is_active = TRUE"
            ),
            {"source": source},
        )
        row = result.fetchone()

        # Per place_type breakdown
        types_result = await db.execute(
            text(
                "SELECT place_type, COUNT(*) as cnt, "
                "AVG(quality_score) FILTER (WHERE quality_score IS NOT NULL) as avg_score,"
                "COUNT(*) FILTER (WHERE quality_score IS NOT NULL AND quality_score <= 15) as garbage_cnt"
                " FROM entities WHERE source = :source AND is_active = TRUE "
                "GROUP BY place_type ORDER BY cnt DESC LIMIT 20"
            ),
            {"source": source},
        )
        type_breakdown = []
        for r in types_result.fetchall():
            type_breakdown.append(
                {
                    "place_type": r[0],
                    "count": r[1],
                    "avg_score": round(r[2], 1) if r[2] else None,
                    "garbage_count": r[3] or 0,
                }
            )

        # Sample IDs per tier
        samples_result = await db.execute(
            text(
                "SELECT id, name, quality_score, place_type "
                "FROM entities WHERE source = :source AND is_active = TRUE "
                "AND quality_score IS NOT NULL "
                "AND (quality_score <= 5 OR quality_score BETWEEN 20 AND 25 OR quality_score BETWEEN 45 AND 50 OR quality_score BETWEEN 70 AND 75 OR quality_score >= 90) "
                "ORDER BY quality_score NULLS LAST LIMIT 20"
            ),
            {"source": source},
        )
        samples = [
            {"id": str(r[0]), "name": r[1], "score": r[2], "place_type": r[3]}
            for r in samples_result.fetchall()
        ]

        return ScriptResult(
            success=True,
            message=f"Total: {row[0]}, Unscored: {row[1]}, Scored: {row[0] - (row[1] or 0)}",
            affected_count=row[0] - (row[1] or 0),
            details=[
                {
                    "distribution": {
                        "garbage": row[2] or 0,
                        "low": row[3] or 0,
                        "medium": row[4] or 0,
                        "good": row[5] or 0,
                        "excellent": row[6] or 0,
                    },
                    "avg_score": round(row[7], 1) if row[7] else None,
                    "min_score": row[8],
                    "max_score": row[9],
                },
                {"place_type_breakdown": type_breakdown},
                {"samples": samples},
            ],
        )

    async def _cleanup(self, db, source: str, min_score: int, dry_run: bool) -> ScriptResult:
        """Hard-delete entities below min_score threshold."""
        from sqlalchemy import text

        count_result = await db.execute(
            text(
                "SELECT COUNT(*) FROM entities "
                "WHERE source = :source AND quality_score IS NOT NULL "
                "AND quality_score < :min_score"
            ),
            {"source": source, "min_score": min_score},
        )
        to_delete = count_result.scalar() or 0

        if not to_delete:
            return ScriptResult(
                success=True,
                message=f"No entities below score {min_score} found",
                affected_count=0,
            )

        if not dry_run:
            await db.execute(
                text(
                    "DELETE FROM entities "
                    "WHERE source = :source "
                    "AND quality_score IS NOT NULL "
                    "AND quality_score < :min_score"
                ),
                {"source": source, "min_score": min_score},
            )
            await db.commit()

        action = "Would delete" if dry_run else "Deleted"
        return ScriptResult(
            success=True,
            message=f"{action} {to_delete} entities scoring below {min_score}",
            affected_count=to_delete,
            details=[{"min_score_threshold": min_score, "dry_run": dry_run}],
        )
