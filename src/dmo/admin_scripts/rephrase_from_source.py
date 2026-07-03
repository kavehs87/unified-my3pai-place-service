import json
import os
import re
from contextlib import suppress

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult

logger = __import__("structlog").get_logger(__name__)


def _make_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:255]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


class RephraseFromSource(AdminScript):
    meta = ScriptMeta(
        name="rephrase_from_source",
        description=(
            "Rephrase name/summary/description via LLM and create new entities "
            "under a target source (e.g. rexby -> my3pai). Original records stay intact."
        ),
        category="Enrich",
        parameters=[
            ScriptParameter(
                name="source",
                type="select",
                label="Source",
                options=["*"],
                default="*",
                description="Source to read from",
            ),
            ScriptParameter(
                name="target_source",
                type="text",
                label="Target Source",
                default="my3pai",
                description="Source name for new entities",
            ),
            ScriptParameter(
                name="prefix",
                type="text",
                label="Source ID Prefix",
                default="rx:",
                description="Prefix for new source_id (e.g. rx:)",
            ),
            ScriptParameter(
                name="max_entities",
                type="int",
                label="Max Entities",
                default=0,
                description="Limit entities to process (0 = unlimited)",
            ),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(
                name="batch_size",
                type="int",
                label="LLM Batch Size",
                default=5,
                description="Entities per LLM batch (rate limiting)",
            ),
            ScriptParameter(
                name="db_batch_size",
                type="int",
                label="DB Batch Size",
                default=50,
                description="Entities per DB commit batch",
            ),
            ScriptParameter(
                name="llm_temperature",
                type="text",
                label="LLM Temperature",
                default="1.0",
                description="LLM creativity (0.0-2.0, higher = more different)",
            ),
            ScriptParameter(
                name="entity_id",
                type="text",
                label="Entity ID",
                default="",
                description="Target specific entity UUID (for testing)",
            ),
        ],
    )

    STOP_FILE = ".stop"

    SYSTEM_PROMPT = (
        "You are a professional tourism content writer. Rephrase the following "
        "tourism entity data. Make it sound fresh and original — not like a copy "
        "of the source — while preserving the factual meaning and intent."
    )

    USER_PROMPT_TEMPLATE = (
        "Rules:\n"
        "- Name: catchy but accurate (max 100 chars)\n"
        "- Summary: 1-2 sentences capturing the essence\n"
        "- Description: 2-4 paragraphs, engaging and informative. "
        "Strip any HTML tags. Write in plain text.\n"
        "- If any field is missing/empty, omit it from the output\n\n"
        "Return ONLY valid JSON, no markdown, no explanation:\n"
        "{{\n"
        '  "rephrased_name": "...",\n'
        '  "rephrased_summary": "...",\n'
        '  "rephrased_description": "..."\n'
        "}}\n\n"
        "Entity data:\n"
        "Name: {name}\n"
        "Summary: {summary}\n"
        "Description: {description}"
    )

    async def _check_stop(self) -> bool:
        return os.path.exists(self.STOP_FILE)

    async def _clear_stop(self) -> None:
        if os.path.exists(self.STOP_FILE):
            os.remove(self.STOP_FILE)

    async def run(self, params, db, llm=None, progress_callback=None) -> ScriptResult:
        source = params.get("source", "*")
        target_source = params.get("target_source", "my3pai")
        prefix = params.get("prefix", "rx:")
        max_entities = int(params.get("max_entities", 0))
        dry_run = params.get("dry_run", True)
        db_batch_size = int(params.get("db_batch_size", 50))
        llm_temperature = float(params.get("llm_temperature", 1.0))
        target_entity_id = params.get("entity_id", "")

        if not llm:
            return ScriptResult(
                success=False,
                message="LLM not configured - set up in Settings first",
                affected_count=0,
            )

        from sqlalchemy import text

        conditions = ["is_active = TRUE"]
        if source and source != "*":
            conditions.append("source = :source")
        if target_entity_id:
            conditions.append("id = :entity_id")

        where = " AND ".join(conditions)

        # Get total count for progress tracking
        count_sql = text(f"SELECT COUNT(*) FROM entities WHERE {where}")
        count_params: dict = {}
        if source and source != "*":
            count_params["source"] = source
        if target_entity_id:
            count_params["entity_id"] = target_entity_id
        total_count = (await db.execute(count_sql, count_params)).scalar() or 0

        if max_entities > 0:
            total_count = min(total_count, max_entities)

        if total_count == 0:
            return ScriptResult(
                success=True,
                message="No entities found matching criteria",
                affected_count=0,
            )

        # Get all processed source_ids for resume
        resume_sql = text("SELECT source_id FROM my3pai_rephrased WHERE source = :source")
        resume_result = await db.execute(resume_sql, {"source": target_source})
        processed_source_ids = set(row[0] for row in resume_result.fetchall())

        processed = 0
        created = 0
        errors = 0
        stop_requested = False
        seen_source_ids: set[str] = set(processed_source_ids)

        try:
            while True:
                if await self._check_stop():
                    stop_requested = True
                    logger.info("rephrase_stop_requested", target_source=target_source)
                    break

                batch_where = conditions[:]
                limit = max_entities - processed if max_entities > 0 else db_batch_size
                limit = min(limit, db_batch_size)

                fetch_sql = text(
                    "SELECT id, source, source_id, name, summary, description, "
                    "description_format, place_type, secondary_types, "
                    "latitude, longitude, country, region, locality, region_names, "
                    "attributes, source_url, thumbnail_url, website, "
                    "is_free, is_open, opening_hours, business_status, "
                    "phone, email, access_type, recommended_season, "
                    "is_barrier_free, rating, favorite_count, currency, price_level "
                    f"FROM entities WHERE {' AND '.join(batch_where)} "
                    f"ORDER BY source_id LIMIT :limit"
                )
                fetch_params: dict = {"limit": limit}
                if source and source != "*":
                    fetch_params["source"] = source

                result = await db.execute(fetch_sql, fetch_params)
                rows = result.fetchall()

                if not rows:
                    break

                batch_created = 0
                for row in rows:
                    if await self._check_stop():
                        stop_requested = True
                        break

                    orig_source_id = row[2]
                    new_source_id = f"{prefix}{orig_source_id}"

                    # Skip if already seen (resume support)
                    if new_source_id in seen_source_ids:
                        processed += 1
                        continue

                    seen_source_ids.add(new_source_id)
                    orig_name = row[3] or ""
                    orig_summary = row[4] or ""
                    orig_description = row[5] or ""
                    orig_place_type = row[7] or ""
                    orig_secondary_types = row[8]
                    orig_lat = row[9]
                    orig_lon = row[10]
                    orig_country = row[11]
                    orig_region = row[12]
                    orig_locality = row[13]
                    orig_region_names = row[14]
                    orig_attributes = row[15] or {}
                    orig_source_url = row[16]
                    orig_thumbnail = row[17]
                    orig_website = row[18]
                    orig_is_free = row[19]
                    orig_is_open = row[20]
                    orig_opening_hours = row[21]
                    orig_business_status = row[22]
                    orig_phone = row[23]
                    orig_email = row[24]
                    orig_access_type = row[25]
                    orig_season = row[26]
                    orig_barrier_free = row[27]
                    orig_rating = row[28]
                    orig_fav_count = row[29]
                    orig_currency = row[30]
                    orig_price_level = row[31]

                    # Check for collision
                    if not dry_run:
                        collision_check = text(
                            "SELECT 1 FROM entities "
                            "WHERE source = :target AND source_id = :sid LIMIT 1"
                        )
                        collision = await db.execute(
                            collision_check,
                            {"target": target_source, "sid": new_source_id},
                        )
                        if collision.scalar():
                            await db.rollback()
                            return ScriptResult(
                                success=False,
                                message=(
                                    f"Source ID collision: {target_source}/{new_source_id} "
                                    f"already exists. Aborting."
                                ),
                                affected_count=created,
                                details=[{"collision": new_source_id}],
                            )

                    # Build prompt
                    prompt = self.USER_PROMPT_TEMPLATE.format(
                        name=orig_name,
                        summary=orig_summary,
                        description=orig_description,
                    )

                    try:
                        response = await llm.chat(
                            [
                                {"role": "system", "content": self.SYSTEM_PROMPT},
                                {"role": "user", "content": prompt},
                            ],
                            temperature=llm_temperature,
                            max_tokens=1024,
                        )
                    except Exception as e:
                        errors += 1
                        logger.error(
                            "rephrase_llm_error",
                            source_id=orig_source_id,
                            error=str(e),
                        )
                        if progress_callback:
                            pct = (processed / total_count) * 100
                            await progress_callback(
                                pct,
                                f"Processed {processed}/{total_count} "
                                f"(created: {created}, errors: {errors})",
                            )
                        continue

                    # Parse JSON response
                    try:
                        cleaned = response.strip()
                        if cleaned.startswith("```"):
                            cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
                            cleaned = re.sub(r"\n?```$", "", cleaned)
                        rephrased = json.loads(cleaned)
                    except (json.JSONDecodeError, AttributeError) as e:
                        errors += 1
                        logger.error(
                            "rephrase_json_parse_error",
                            source_id=orig_source_id,
                            error=str(e),
                            raw_response=response[:200],
                        )
                        if progress_callback:
                            pct = (processed / total_count) * 100
                            await progress_callback(
                                pct,
                                f"Processed {processed}/{total_count} "
                                f"(created: {created}, errors: {errors})",
                            )
                        continue

                    new_name = rephrased.get("rephrased_name", "").strip()
                    new_summary = rephrased.get("rephrased_summary", "").strip()
                    new_description = rephrased.get("rephrased_description", "").strip()

                    if not new_name:
                        errors += 1
                        logger.warning(
                            "rephrase_empty_name",
                            source_id=orig_source_id,
                        )
                        if progress_callback:
                            pct = (processed / total_count) * 100
                            await progress_callback(
                                pct,
                                f"Processed {processed}/{total_count} "
                                f"(created: {created}, errors: {errors})",
                            )
                        continue

                    new_slug = _make_slug(new_name)
                    clean_description = _strip_html(new_description) if new_description else None

                    if dry_run:
                        created += 1
                        batch_created += 1
                        if progress_callback:
                            pct = (processed / total_count) * 100
                            await progress_callback(
                                pct,
                                f"Processed {processed}/{total_count} "
                                f"(created: {created}, errors: {errors})",
                            )
                        continue

                    # Insert entity via ORM
                    from dmo.models.database import Entity

                    entity = Entity(
                        source=target_source,
                        source_id=new_source_id,
                        source_url=orig_source_url,
                        name=new_name,
                        slug=new_slug,
                        summary=new_summary if new_summary else None,
                        description=clean_description,
                        description_format="text",
                        place_type=orig_place_type,
                        secondary_types=orig_secondary_types,
                        latitude=orig_lat,
                        longitude=orig_lon,
                        country=orig_country,
                        region=orig_region,
                        locality=orig_locality,
                        region_names=orig_region_names,
                        thumbnail_url=orig_thumbnail,
                        website=orig_website,
                        is_free=orig_is_free or False,
                        is_open=orig_is_open,
                        opening_hours=orig_opening_hours,
                        business_status=orig_business_status,
                        phone=orig_phone,
                        email=orig_email,
                        access_type=orig_access_type,
                        recommended_season=orig_season,
                        is_barrier_free=orig_barrier_free or False,
                        rating=orig_rating,
                        favorite_count=orig_fav_count or 0,
                        currency=orig_currency,
                        price_level=orig_price_level,
                        attributes=orig_attributes,
                        is_active=True,
                    )

                    db.add(entity)
                    await db.flush()

                    # Set PostGIS location if coordinates present
                    if orig_lat is not None and orig_lon is not None:
                        from sqlalchemy import text as sql_text

                        await db.execute(
                            sql_text(
                                "UPDATE entities SET location = ST_SetSRID("
                                "ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid"
                            ).bindparams(lat=orig_lat, lon=orig_lon, eid=entity.id)
                        )

                    # Record in state table
                    await db.execute(
                        text(
                            "INSERT INTO my3pai_rephrased (source, source_id, entity_id) "
                            "VALUES (:source, :sid, :eid)"
                        ),
                        {
                            "source": target_source,
                            "sid": new_source_id,
                            "eid": str(entity.id),
                        },
                    )

                    created += 1
                    batch_created += 1

                    if progress_callback:
                        pct = (processed / total_count) * 100
                        await progress_callback(
                            pct,
                            f"Processed {processed}/{total_count} "
                            f"(created: {created}, errors: {errors})",
                        )

                if not dry_run:
                    await db.commit()

                # Break if no new entities were created in this batch
                if batch_created == 0 and processed > 0:
                    break

                if stop_requested:
                    break

        except Exception as e:
            logger.error("rephrase_script_error", error=str(e), exc_info=True)
            if not dry_run:
                with suppress(Exception):
                    await db.rollback()
            return ScriptResult(
                success=False,
                message=f"Script error: {str(e)}",
                affected_count=created,
            )
        finally:
            if not dry_run:
                await self._clear_stop()

        if stop_requested:
            status_msg = "stopped"
        elif dry_run:
            status_msg = "would create"
        else:
            status_msg = "created"

        msg = (
            f"{status_msg} {created} entities as {target_source} "
            f"(processed: {processed}, errors: {errors})"
        )
        if stop_requested:
            msg += " [STOPPED]"

        return ScriptResult(
            success=True,
            message=msg,
            affected_count=created,
            details=[],
        )
