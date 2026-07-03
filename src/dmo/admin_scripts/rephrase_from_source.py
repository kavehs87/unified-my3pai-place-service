import asyncio
import json
import os
import re
from contextlib import suppress

from sqlalchemy import text
from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult

logger = __import__("structlog").get_logger(__name__)

OPENCODE_ZEN_API_KEY = "sk-l1Kyv57RsQ0QnUqrRW0kJGtCqj36jcXg0V4Tz6Xqph6AmCZxQHkCBzugnJkoyn0G"
OPENCODE_ZEN_BASE_URL = "https://opencode.ai/zen/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_MAX_RETRIES = 2


def _make_slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    slug = re.sub(r"[\s]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")[:255]


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


def _validate_rephrased(entity_data: dict, rephrased: dict) -> dict:
    """Validate rephrased output meets quality thresholds.
    
    Returns: {"valid": bool, "issues": [str], "name_length": int, "summary_length": int, "description_length": int}
    """
    issues = []
    
    name = rephrased.get("rephrased_name", "") or ""
    summary = rephrased.get("rephrased_summary", "") or ""
    description = rephrased.get("rephrased_description", "") or ""
    
    name_length = len(name)
    summary_length = len(summary)
    description_length = len(description)
    
    # Name checks
    if not name:
        issues.append("empty_name")
    elif name_length < 20:
        issues.append(f"name_too_short ({name_length} chars)")
    elif name_length > 200:
        issues.append(f"name_too_long ({name_length} chars)")
    
    # Summary checks
    if not summary:
        issues.append("empty_summary")
    elif summary_length < 20:
        issues.append(f"summary_too_short ({summary_length} chars)")
    
    # Description checks
    if not description:
        issues.append("empty_description")
    elif description_length < 50:
        issues.append(f"description_too_short ({description_length} chars)")
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "name_length": name_length,
        "summary_length": summary_length,
        "description_length": description_length,
    }


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
                name="concurrency",
                type="int",
                label="LLM Concurrency",
                default=5,
                description="Concurrent LLM calls (3-5 recommended)",
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
        "- Name: catchy but accurate (50-100 chars)\n"
        "- Summary: 1 sentence (50-150 chars)\n"
        "- Description: 1-2 paragraphs (200-400 chars total). "
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

    async def _call_llm(self, llm, prompt: str, temperature: float, max_retries: int = 3) -> str:
        """Call LLM with error handling and retries."""
        last_error = None
        for attempt in range(max_retries):
            try:
                response = await llm.chat(
                    [
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=DEFAULT_MAX_TOKENS,
                )
                if not response or not response.strip():
                    if attempt < max_retries - 1:
                        logger.warning("rephrase_empty_response_retry", attempt=attempt + 1, max_retries=max_retries)
                        await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
                        continue
                    raise ValueError("Empty response from LLM after retries")
                return response
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    logger.warning("rephrase_llm_error_retry", error=str(e), attempt=attempt + 1, max_retries=max_retries)
                    await asyncio.sleep(1 * (attempt + 1))
                    continue
                logger.error("rephrase_llm_error", error=str(e), exc_info=True)
                raise last_error
        raise last_error or ValueError("Unexpected: no retries attempted")

    async def _parse_llm_response(self, response: str) -> dict:
        """Parse LLM JSON response with error handling."""
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
                cleaned = re.sub(r"\n?```$", "", cleaned)
            
            # Skip reasoning content - look for JSON object
            # Models like DeepSeek sometimes put reasoning before the actual JSON
            if not cleaned.startswith("{"):
                # Find the first { which should be the start of JSON
                start = cleaned.find("{")
                if start != -1:
                    # Find the matching closing }
                    brace_count = 0
                    end = -1
                    for i in range(start, len(cleaned)):
                        if cleaned[i] == "{":
                            brace_count += 1
                        elif cleaned[i] == "}":
                            brace_count -= 1
                            if brace_count == 0:
                                end = i
                                break
                    if end != -1:
                        cleaned = cleaned[start:end+1]
                    else:
                        cleaned = cleaned[start:]
                else:
                    # No JSON found - this is pure reasoning content
                    raise ValueError("No JSON found in response (reasoning only)")
            
            # Try parsing as-is first
            try:
                rephrased = json.loads(cleaned)
                return rephrased
            except json.JSONDecodeError:
                pass
            
            # If that fails, try to fix common issues with newlines in strings
            fixed = []
            in_string = False
            escape_next = False
            
            for char in cleaned:
                if escape_next:
                    fixed.append(char)
                    escape_next = False
                    continue
                
                if char == "\\" and in_string:
                    fixed.append(char)
                    escape_next = True
                    continue
                
                if char == '"' and not escape_next:
                    in_string = not in_string
                    fixed.append(char)
                    continue
                
                if char == "\n" and in_string:
                    fixed.append("\\n")
                    continue
                
                fixed.append(char)
            
            fixed_cleaned = "".join(fixed)
            
            # Try parsing again with fixed newlines
            try:
                rephrased = json.loads(fixed_cleaned)
                return rephrased
            except json.JSONDecodeError:
                pass
            
            raise
            
        except (json.JSONDecodeError, AttributeError, ValueError) as e:
            logger.error(
                "rephrase_json_parse_error",
                error=str(e),
                raw_response=response[:500],
            )
            raise

    async def _process_entity(
        self,
        entity_data: dict,
        llm,
        db,
        target_source: str,
        prefix: str,
        dry_run: bool,
        semaphore: asyncio.Semaphore,
        llm_temperature: float,
    ) -> dict:
        """Process a single entity with LLM call and DB insert.
        
        Returns: {"success": bool, "entity_id": str, "error": str, "quality": dict, "rephrased": dict}
        """
        orig_source_id = entity_data["orig_source_id"]
        new_source_id = f"{prefix}{orig_source_id}"
        
        async with semaphore:
            # Build prompt
            prompt = self.USER_PROMPT_TEMPLATE.format(
                name=entity_data["orig_name"],
                summary=entity_data["orig_summary"],
                description=entity_data["orig_description"],
            )
            
            # Call LLM with retries for reasoning-only responses
            response = None
            for attempt in range(DEFAULT_MAX_RETRIES + 1):
                try:
                    response = await self._call_llm(llm, prompt, llm_temperature, max_retries=1)
                    # Try to parse - if it fails with reasoning error, retry
                    try:
                        rephrased = await self._parse_llm_response(response)
                        break  # Success
                    except ValueError as e:
                        if "reasoning only" in str(e) and attempt < DEFAULT_MAX_RETRIES:
                            logger.warning("rephrase_reasoning_retry", attempt=attempt + 1)
                            await asyncio.sleep(1)
                            continue
                        raise
                except Exception as e:
                    if attempt < DEFAULT_MAX_RETRIES:
                        logger.warning("rephrase_llm_retry", error=str(e), attempt=attempt + 1)
                        await asyncio.sleep(1)
                        continue
                    return {
                        "success": False,
                        "entity_id": None,
                        "error": f"LLM error: {str(e)}",
                        "quality": None,
                        "rephrased": None,
                        "entity_data": entity_data,
                    }
            
            if response is None:
                return {
                    "success": False,
                    "entity_id": None,
                    "error": "No valid response after retries",
                    "quality": None,
                    "rephrased": None,
                    "entity_data": entity_data,
                }
            
            # Parse JSON (already validated above, but handle edge cases)
            try:
                rephrased = await self._parse_llm_response(response)
            except (json.JSONDecodeError, AttributeError, ValueError) as e:
                return {
                    "success": False,
                    "entity_id": None,
                    "error": f"JSON parse error: {str(e)}",
                    "quality": None,
                    "rephrased": None,
                    "entity_data": entity_data,
                }
            
            # Validate
            new_name = rephrased.get("rephrased_name", "") or ""
            new_summary = rephrased.get("rephrased_summary", "") or ""
            new_description = rephrased.get("rephrased_description", "") or ""
            
            quality = _validate_rephrased(entity_data, rephrased)
            
            if not new_name:
                return {
                    "success": False,
                    "entity_id": None,
                    "error": "empty_name",
                    "quality": quality,
                    "rephrased": rephrased,
                    "entity_data": entity_data,
                }
            
            if not dry_run:
                # Check for collision
                collision_check = text(
                    "SELECT 1 FROM entities "
                    "WHERE source = :target AND source_id = :sid LIMIT 1"
                )
                collision = await db.execute(
                    collision_check,
                    {"target": target_source, "sid": new_source_id},
                )
                if collision.scalar():
                    return {
                        "success": False,
                        "entity_id": None,
                        "error": f"collision: {new_source_id}",
                        "quality": quality,
                        "rephrased": rephrased,
                        "entity_data": entity_data,
                    }
                
                # Insert entity via ORM
                from dmo.models.database import Entity
                
                entity = Entity(
                    source=target_source,
                    source_id=new_source_id,
                    source_url=entity_data["orig_source_url"],
                    name=new_name,
                    slug=_make_slug(new_name),
                    summary=new_summary if new_summary else None,
                    description=_strip_html(new_description) if new_description else None,
                    description_format="text",
                    place_type=entity_data["orig_place_type"],
                    secondary_types=entity_data["orig_secondary_types"],
                    latitude=entity_data["orig_lat"],
                    longitude=entity_data["orig_lon"],
                    country=entity_data["orig_country"],
                    region=entity_data["orig_region"],
                    locality=entity_data["orig_locality"],
                    region_names=entity_data["orig_region_names"],
                    thumbnail_url=entity_data["orig_thumbnail"],
                    website=entity_data["orig_website"],
                    is_free=entity_data["orig_is_free"] or False,
                    is_open=entity_data["orig_is_open"],
                    opening_hours=entity_data["orig_opening_hours"],
                    business_status=entity_data["orig_business_status"],
                    phone=entity_data["orig_phone"],
                    email=entity_data["orig_email"],
                    access_type=entity_data["orig_access_type"],
                    recommended_season=entity_data["orig_season"],
                    is_barrier_free=entity_data["orig_barrier_free"] or False,
                    rating=entity_data["orig_rating"],
                    favorite_count=entity_data["orig_fav_count"] or 0,
                    currency=entity_data["orig_currency"],
                    price_level=entity_data["orig_price_level"],
                    attributes=entity_data["orig_attributes"],
                    is_active=True,
                )
                
                db.add(entity)
                await db.flush()
                
                # Set PostGIS location if coordinates present
                if entity_data["orig_lat"] is not None and entity_data["orig_lon"] is not None:
                    await db.execute(
                        text(
                            "UPDATE entities SET location = ST_SetSRID("
                            "ST_MakePoint(:lon, :lat), 4326) WHERE id = :eid"
                        ).bindparams(
                            lat=entity_data["orig_lat"],
                            lon=entity_data["orig_lon"],
                            eid=entity.id,
                        )
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
                
                return {
                    "success": True,
                    "entity_id": str(entity.id),
                    "error": None,
                    "quality": quality,
                    "rephrased": rephrased,
                    "entity_data": entity_data,
                }
            else:
                return {
                    "success": True,
                    "entity_id": None,
                    "error": None,
                    "quality": quality,
                    "rephrased": rephrased,
                    "entity_data": entity_data,
                }

    def _print_sample_outputs(self, samples: list[dict]):
        """Print 5 sample outputs for visual review."""
        print("\n=== Sample Output (Concurrency=5, Dry-Run) ===\n")
        
        for i, sample in enumerate(samples[:5], 1):
            print(f"Entity {i}: {sample['original_name']}")
            print(f"  Original Summary: {sample['original_summary'][:80]}...")
            print(f"  Rephrased Name: {sample['rephrased_name']}")
            print(f"  Rephrased Summary: {sample['rephrased_summary'][:80]}...")
            print(f"  Quality: {'✅ Good' if sample['valid'] else '⚠️ Issues'}")
            if sample['issues']:
                print(f"  Issues: {', '.join(sample['issues'])}")
            print()
        
        print("=== Summary ===")
        valid_count = sum(1 for s in samples if s["valid"])
        print(f"- {len(samples)} entities processed")
        print(f"- {valid_count}/{len(samples)} passed quality checks")
        print()
        
        if valid_count == len(samples):
            print("✅ All checks passed — proceed to live run?")
        else:
            print("⚠️ Some quality issues detected — review before proceeding")

    async def run(self, params, db, llm=None, progress_callback=None) -> ScriptResult:
        source = params.get("source", "*")
        target_source = params.get("target_source", "my3pai")
        prefix = params.get("prefix", "rx:")
        max_entities = int(params.get("max_entities", 0))
        dry_run = params.get("dry_run", True)
        db_batch_size = int(params.get("db_batch_size", 50))
        llm_temperature = float(params.get("llm_temperature", 1.0))
        concurrency = int(params.get("concurrency", 5))
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
        quality_results: list[dict] = []
        sample_outputs: list[dict] = []

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

                # Prepare entity batch
                entity_batch = []
                for row in rows:
                    orig_source_id = row[2]
                    new_source_id = f"{prefix}{orig_source_id}"
                    
                    # Skip if already seen (resume support)
                    if new_source_id in seen_source_ids:
                        processed += 1
                        continue
                    
                    seen_source_ids.add(new_source_id)
                    
                    entity_batch.append({
                        "orig_source_id": orig_source_id,
                        "new_source_id": new_source_id,
                        "orig_name": row[3] or "",
                        "orig_summary": row[4] or "",
                        "orig_description": row[5] or "",
                        "orig_place_type": row[7] or "",
                        "orig_secondary_types": row[8],
                        "orig_lat": row[9],
                        "orig_lon": row[10],
                        "orig_country": row[11],
                        "orig_region": row[12],
                        "orig_locality": row[13],
                        "orig_region_names": row[14],
                        "orig_attributes": row[15] or {},
                        "orig_source_url": row[16],
                        "orig_thumbnail": row[17],
                        "orig_website": row[18],
                        "orig_is_free": row[19],
                        "orig_is_open": row[20],
                        "orig_opening_hours": row[21],
                        "orig_business_status": row[22],
                        "orig_phone": row[23],
                        "orig_email": row[24],
                        "orig_access_type": row[25],
                        "orig_season": row[26],
                        "orig_barrier_free": row[27],
                        "orig_rating": row[28],
                        "orig_fav_count": row[29],
                        "orig_currency": row[30],
                        "orig_price_level": row[31],
                    })

                if not entity_batch:
                    break

                # Process in parallel
                semaphore = asyncio.Semaphore(concurrency)
                tasks = [
                    self._process_entity(
                        entity,
                        llm,
                        db,
                        target_source,
                        prefix,
                        dry_run,
                        semaphore,
                        llm_temperature,
                    )
                    for entity in entity_batch
                ]

                # Gather results
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Count successes/errors
                batch_created = 0
                for result in results:
                    if isinstance(result, Exception):
                        errors += 1
                        logger.error(
                            "rephrase_entity_error",
                            error=str(result),
                            exc_info=True,
                        )
                    elif isinstance(result, dict):
                        if result.get("success"):
                            created += 1
                            batch_created += 1
                        else:
                            errors += 1
                            error_msg = result.get("error", "unknown")
                            logger.warning(
                                "rephrase_entity_failed",
                                error=error_msg,
                            )
                        
                        # Track quality for all results (success or fail)
                        if result.get("quality"):
                            quality_results.append(result["quality"])
                        
                        if dry_run and len(sample_outputs) < 5:
                            rephrased = result.get("rephrased", {})
                            quality = result.get("quality", {})
                            entity = result.get("entity_data", {})
                            sample_outputs.append({
                                "original_name": entity.get("orig_name", ""),
                                "original_summary": entity.get("orig_summary", ""),
                                "rephrased_name": rephrased.get("rephrased_name", "") if rephrased else "",
                                "rephrased_summary": rephrased.get("rephrased_summary", "") if rephrased else "",
                                "valid": quality.get("valid", False) if quality else False,
                                "issues": quality.get("issues", []) if quality else [],
                            })
                    else:
                        errors += 1
                        logger.warning(
                            "rephrase_entity_failed",
                            error=str(result),
                        )

                processed += len(entity_batch)

                if progress_callback:
                    pct = (processed / total_count) * 100
                    await progress_callback(
                        pct,
                        f"Processed {processed}/{total_count} "
                        f"(created: {created}, errors: {errors}, "
                        f"concurrency: {concurrency})"
                    )

                # Print sample outputs for dry-run
                if dry_run and len(sample_outputs) >= 5:
                    self._print_sample_outputs(sample_outputs)

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

        # Quality check for dry-run
        if dry_run and quality_results:
            empty_names = sum(1 for q in quality_results if "empty_name" in q["issues"])
            quality_failures = sum(1 for q in quality_results if not q["valid"])
            failure_rate = quality_failures / len(quality_results) if quality_results else 0

            if empty_names > 0 or failure_rate > 0.25:
                logger.warning(
                    "quality_check_failed",
                    empty_names=empty_names,
                    failure_rate=failure_rate,
                )
                return ScriptResult(
                    success=False,
                    message=f"Quality check failed: {empty_names} empty names, {failure_rate:.0%} failures",
                    affected_count=created,
                    details=[{"fallback": True}],
                )

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
