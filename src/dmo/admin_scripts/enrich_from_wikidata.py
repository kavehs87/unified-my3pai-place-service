import asyncio
import logging
import re
from urllib.parse import quote

import httpx

from dmo.admin_scripts.base import AdminScript, ScriptMeta, ScriptParameter, ScriptResult

logger = logging.getLogger(__name__)

# Secondary wiki key patterns: osm_<prefix>:wikidata, osm_<prefix>:wikipedia
SECONDARY_WIKI_PATTERN = re.compile(r"^osm_(.+):(\w+)$")


class EnrichFromWikidata(AdminScript):
    meta = ScriptMeta(
        name="enrich_from_wikidata",
        description="Enrich OSM entities using Wikidata/Wikipedia APIs. Uses osm_wikidata, osm_wikimedia_commons, osm_wikipedia, and secondary keys (osm_artist:wikidata, osm_subject:wikidata, etc.). Fetches description, summary, website, thumbnail, address, country, opening_hours, phone, email.",
        category="Enrich",
        parameters=[
            ScriptParameter(
                name="max_entities",
                type="int",
                label="Max Entities",
                default=10,
                description="Limit number of entities to process (0 = unlimited)",
            ),
            ScriptParameter(name="dry_run", type="boolean", label="Dry Run", default=True),
            ScriptParameter(
                name="enrich_description",
                type="boolean",
                label="Fetch Wikipedia Extract",
                default=True,
                description="Fetch prose description from Wikipedia REST API (slower)",
            ),
            ScriptParameter(
                name="batch_size",
                type="int",
                label="Wikidata Batch Size",
                default=50,
                description="QIDs per Wikidata API call (max 50)",
            ),
            ScriptParameter(
                name="db_batch_size",
                type="int",
                label="DB Batch Size",
                default=500,
                description="Entities per DB batch (commit after each)",
            ),
            ScriptParameter(
                name="user_agent",
                type="text",
                label="User-Agent",
                default="DMO-Enricher/1.0",
                description="User-Agent header for API requests",
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

    # Wikidata property IDs
    P_OFFICIAL_WEBSITE = "P856"
    P_IMAGE = "P18"
    P_COORDINATES = "P625"
    P_STREET_ADDRESS = "P682"
    P_LOCATED_IN_THE_TERRITORIAL_ENTITY = "P131"
    P_COUNTRY = "P17"
    P_OPENING_HOURS = "P1412"
    P_PHONE = "P426"
    P_EMAIL = "P479"

    WIKIDATA_API = "https://www.wikidata.org/w/api.php"
    WIKIPEDIA_REST = "https://{lang}.wikipedia.org/api/rest_v1/page/extract/{title}"

    def _extract_claim_value(self, claims: dict, pid: str) -> str | None:
        """Extract first string value from a property's claims."""
        if pid not in claims:
            return None
        for claim in claims[pid]:
            if "mainsnak" in claim and claim["mainsnak"]["snaktype"] == "value":
                return claim["mainsnak"]["datavalue"]["value"]
            if "mainsnak" in claim and claim["mainsnak"]["snaktype"] == "somevalue":
                return None
        return None

    def _extract_monolingual_text(self, claims: dict, pid: str) -> str | None:
        """Extract text from a monolingualtext claim (ignoring language)."""
        if pid not in claims:
            return None
        for claim in claims[pid]:
            if "mainsnak" in claim and claim["mainsnak"]["snaktype"] == "value":
                dv = claim["mainsnak"]["datavalue"]["value"]
                if "text" in dv:
                    return dv["text"]
        return None

    def _extract_commons_thumbnail(self, commons_ref: str) -> str | None:
        """Extract thumbnail URL from osm_wikimedia_commons value."""
        if not commons_ref:
            return None
        # Handle "Category:Foo" format - categories need Commons API to get first file
        # For now, skip category references
        return None

    # Column length limits from database schema
    FIELD_LIMITS = {
        "summary": 5000,
        "description": 50000,
        "website": 2048,
        "thumbnail_url": 2048,
        "address": 500,
        "country": 100,
        "opening_hours": 500,
        "phone": 50,
        "email": 255,
    }

    def _set_field(self, updates: dict, field: str, value: str) -> None:
        """Set field in updates dict, truncating to column limit."""
        limit = self.FIELD_LIMITS.get(field)
        if limit and len(value) > limit:
            value = value[:limit]
        updates[field] = value

    def _build_update_dict(
        self,
        entity_attrs: dict,
        wd_entity: dict,
        wp_extract: str | None,
        commons_ref: str | None,
    ) -> dict:
        """Build dict of fields to update from Wikidata entity + Wikipedia extract."""
        updates = {}
        claims = wd_entity.get("claims", {})

        # summary <- Wikidata short description
        if "descriptions" in wd_entity:
            for _lang, desc in wd_entity["descriptions"].items():
                if "value" in desc:
                    if entity_attrs.get("summary") is None:
                        self._set_field(updates, "summary", desc["value"])
                    break

        # description <- Wikipedia extract (first paragraph, max 2000 chars)
        if wp_extract and entity_attrs.get("description") is None:
            clean = wp_extract.strip()
            if clean:
                first_para = clean.split("\n\n")[0]
                self._set_field(updates, "description", first_para[:2000])
        elif entity_attrs.get("description") is None:
            for _lang, desc in wd_entity.get("descriptions", {}).items():
                if "value" in desc:
                    self._set_field(updates, "description", desc["value"])
                    break

        # website <- P856
        website = self._extract_claim_value(claims, self.P_OFFICIAL_WEBSITE)
        if website and entity_attrs.get("website") is None:
            self._set_field(updates, "website", website)

        # thumbnail_url <- P18 (Commons image)
        commons_file = self._extract_claim_value(claims, self.P_IMAGE)
        if commons_file and entity_attrs.get("thumbnail_url") is None:
            file_title = quote(commons_file, safe=":/@!$&'()*+,;=-._~%")
            self._set_field(
                updates,
                "thumbnail_url",
                f"https://commons.wikimedia.org/wiki/Special:FilePath/{file_title}",
            )

        # Fallback: try osm_wikimedia_commons for thumbnail
        if commons_ref and entity_attrs.get("thumbnail_url") is None:
            file_name = commons_ref.replace("Category:", "")
            if not commons_ref.startswith("Category:"):
                file_title = quote(file_name, safe=":/@!$&'()*+,;=-._~%")
                self._set_field(
                    updates,
                    "thumbnail_url",
                    f"https://commons.wikimedia.org/wiki/Special:FilePath/{file_title}",
                )

        # address <- P682 (street address)
        street = self._extract_monolingual_text(claims, self.P_STREET_ADDRESS)
        if street and entity_attrs.get("address") is None:
            self._set_field(updates, "address", street)

        # country <- P17 (country QID) -> human name from labels
        country_qid = self._extract_claim_value(claims, self.P_COUNTRY)
        if country_qid and entity_attrs.get("country") is None:
            if "labels" in wd_entity:
                for lbl in wd_entity["labels"].values():
                    if "value" in lbl:
                        self._set_field(updates, "country", lbl["value"])
                        break

        # opening_hours <- P1412
        oh = self._extract_monolingual_text(claims, self.P_OPENING_HOURS)
        if oh and entity_attrs.get("opening_hours") is None:
            self._set_field(updates, "opening_hours", oh)

        # phone <- P426
        phone = self._extract_claim_value(claims, self.P_PHONE)
        if phone and entity_attrs.get("phone") is None:
            self._set_field(updates, "phone", phone)

        # email <- P479
        email = self._extract_claim_value(claims, self.P_EMAIL)
        if email and entity_attrs.get("email") is None:
            self._set_field(updates, "email", email)

        return updates

    def _collect_wiki_keys(self, attrs: dict) -> dict:
        """Collect all wiki-related keys from entity attributes.

        Returns dict:
          - primary_qid: osm_wikidata value
          - commons: osm_wikimedia_commons value
          - wikipedia: osm_wikipedia value (lang:title)
          - secondary_qids: {key: qid} for osm_<prefix>:wikidata keys
          - secondary_wikis: {key: lang:title} for osm_<prefix>:wikipedia keys
        """
        result = {
            "primary_qid": None,
            "commons": None,
            "wikipedia": None,
            "secondary_qids": {},
            "secondary_wikis": {},
        }

        result["primary_qid"] = attrs.get("osm_wikidata")
        result["commons"] = attrs.get("osm_wikimedia_commons")
        result["wikipedia"] = attrs.get("osm_wikipedia")

        for key, value in attrs.items():
            match = SECONDARY_WIKI_PATTERN.match(key)
            if match:
                prefix, wiki_type = match.groups()
                if wiki_type == "wikidata" and value:
                    result["secondary_qids"][key] = value
                elif wiki_type == "wikipedia" and value:
                    result["secondary_wikis"][key] = value

        return result

    async def _fetch_wikidata_batch(
        self, qids: list[str], client: httpx.AsyncClient, user_agent: str
    ) -> dict[str, dict]:
        """Fetch Wikidata entities for a batch of QIDs. Retries on 429."""
        ids = "|".join(qids)
        params = {
            "action": "wbgetentities",
            "ids": ids,
            "format": "json",
            "props": "claims|descriptions|labels|sitelinks",
            "sitefilter": "enwiki",
        }
        for attempt in range(3):
            try:
                resp = await client.get(
                    self.WIKIDATA_API, params=params, headers={"User-Agent": user_agent}
                )
                if resp.status_code == 429:
                    wait = (attempt + 1) * 5
                    logger.warning("Wikidata rate limited, retrying in %ds", wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                entities = {}
                for qid, entity_data in data.get("entities", {}).items():
                    if not entity_data.get("missing"):
                        entities[qid] = entity_data
                return entities
            except httpx.HTTPError as e:
                logger.error("Wikidata batch fetch failed (attempt %d): %s", attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep((attempt + 1) * 3)
                else:
                    logger.error("Wikidata batch fetch failed after retries, skipping batch")
                    return {}
        return {}

    async def _fetch_wikipedia_extract(
        self, wd_entity: dict, client: httpx.AsyncClient, user_agent: str
    ) -> str | None:
        """Fetch Wikipedia English extract, fallback to other languages."""
        sitelinks = wd_entity.get("sitelinks", {})

        # Try enwiki first
        enlink = sitelinks.get("enwiki", {})
        title = enlink.get("title")

        # Fallback: try any available sitelink
        if not title:
            for sl in sitelinks.values():
                if sl.get("title"):
                    title = sl["title"]
                    break

        if not title:
            return None

        encoded_title = title.replace(" ", "_")
        url = self.WIKIPEDIA_REST.format(lang="en", title=encoded_title)

        try:
            async with client.stream(
                "GET",
                url,
                headers={"User-Agent": user_agent, "Accept": "application/x.wiki"},
                timeout=10,
            ) as resp:
                if resp.status_code == 200:
                    wiki_text = await resp.text()
                    lines = [line.strip() for line in wiki_text.split("\n") if line.strip()]
                    clean_lines = []
                    for line in lines:
                        if line.startswith(("{|", "|", "==", "#", "*", "[[", "__")):
                            break
                        if line.startswith("[") and "]" in line:
                            continue
                        clean_lines.append(line)
                        if len(clean_lines) >= 5:
                            break
                    return " ".join(clean_lines) if clean_lines else None
        except (httpx.HTTPError, TimeoutError):
            pass
        return None

    async def run(self, params, db, llm=None, progress_callback=None) -> ScriptResult:
        max_entities = int(params.get("max_entities", 10))
        dry_run = params.get("dry_run", True)
        enrich_description = params.get("enrich_description", True)
        api_batch_size = min(int(params.get("batch_size", 50)), 50)
        user_agent = params.get("user_agent", "DMO-Enricher/1.0")
        target_entity_id = params.get("entity_id", "")
        db_batch_size = int(params.get("db_batch_size", 500))

        from sqlalchemy import text

        # Build base WHERE clauses
        wiki_conditions = [
            "(attributes->>'osm_wikidata') IS NOT NULL",
            "(attributes->>'osm_wikimedia_commons') IS NOT NULL",
            "(attributes->>'osm_wikipedia') IS NOT NULL",
        ]
        base_where = [
            "source = 'osm'",
            "is_active = true",
            "quality_score >= 15",
            "enriched_at IS NULL",
            f"({' OR '.join(wiki_conditions)})",
        ]
        if target_entity_id:
            base_where.append(f"id = '{target_entity_id}'")

        # Get total count for progress tracking
        count_query = text(f"SELECT count(*) FROM entities WHERE {' AND '.join(base_where)}")
        total_count = (await db.execute(count_query)).scalar()
        if total_count == 0:
            return ScriptResult(
                success=True,
                message="No OSM entities with wiki keys found",
                affected_count=0,
            )

        if max_entities > 0:
            total_count = min(total_count, max_entities)

        enriched_count = 0
        skipped_count = 0
        not_found_count = 0
        processed = 0
        last_id = None

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                # Fetch DB batch using keyset pagination
                batch_where = base_where[:]
                if last_id:
                    batch_where.append("id >= :last_id")
                limit = max_entities - processed if max_entities > 0 else db_batch_size
                limit = min(limit, db_batch_size)

                query = text(
                    "SELECT id, attributes, summary, description, website, "
                    "thumbnail_url, address, country, opening_hours, phone, email "
                    f"FROM entities WHERE {' AND '.join(batch_where)} "
                    f"ORDER BY id LIMIT :limit"
                )
                result = await db.execute(query, {"last_id": last_id, "limit": limit})
                rows = result.fetchall()

                if not rows:
                    break

                # Collect QIDs for this batch
                batch_entities = []
                all_qids = set()
                for row in rows:
                    eid = row[0]
                    attrs = row[1] or {}
                    wiki_keys = self._collect_wiki_keys(attrs)
                    current = {
                        "summary": row[2],
                        "description": row[3],
                        "website": row[4],
                        "thumbnail_url": row[5],
                        "address": row[6],
                        "country": row[7],
                        "opening_hours": row[8],
                        "phone": row[9],
                        "email": row[10],
                    }

                    if wiki_keys["primary_qid"]:
                        all_qids.add(wiki_keys["primary_qid"])
                    for qid in wiki_keys["secondary_qids"].values():
                        all_qids.add(qid)

                    batch_entities.append((eid, wiki_keys, current))
                    last_id = eid

                # Fetch Wikidata for this batch's QIDs
                wd_entities = {}
                if all_qids:
                    qid_list = list(all_qids)
                    for i in range(0, len(qid_list), api_batch_size):
                        batch_qids = qid_list[i : i + api_batch_size]
                        wd_entities.update(
                            await self._fetch_wikidata_batch(batch_qids, client, user_agent)
                        )
                        if i + api_batch_size < len(qid_list):
                            await asyncio.sleep(0.5)

                # Build QID → entity lookup for this batch
                qid_to_entities = {}
                for eid, wiki_keys, current in batch_entities:
                    if wiki_keys["primary_qid"]:
                        qid_to_entities.setdefault(wiki_keys["primary_qid"], []).append(
                            (eid, wiki_keys, current)
                        )
                    for _key, qid in wiki_keys["secondary_qids"].items():
                        qid_to_entities.setdefault(qid, []).append((eid, wiki_keys, current))

                # Process each entity in batch
                seen_ids = set()
                for primary_qid, entity_list in qid_to_entities.items():
                    entity_id, wiki_keys, current = entity_list[0]
                    if entity_id in seen_ids:
                        continue
                    seen_ids.add(entity_id)
                    processed += 1

                    # Get primary Wikidata entity
                    wd_entity = wd_entities.get(primary_qid)

                    # Try secondary QIDs if primary not found
                    if not wd_entity and wiki_keys["secondary_qids"]:
                        for _key, qid in wiki_keys["secondary_qids"].items():
                            if qid in wd_entities:
                                wd_entity = wd_entities[qid]
                                break

                    if not wd_entity:
                        not_found_count += 1
                        continue

                    # Build update dict from Wikidata
                    updates = self._build_update_dict(
                        current, wd_entity, None, wiki_keys["commons"]
                    )

                    # Fetch Wikipedia extract if needed
                    wp_extract = None
                    if enrich_description and current.get("description") is None:
                        wp_extract = await self._fetch_wikipedia_extract(
                            wd_entity, client, user_agent
                        )
                        await asyncio.sleep(0.3)

                    # Rebuild updates with Wikipedia extract
                    if wp_extract:
                        updates = self._build_update_dict(
                            current, wd_entity, wp_extract, wiki_keys["commons"]
                        )

                    if updates:
                        enriched_count += 1
                        if not dry_run:
                            set_clauses = []
                            values = {"id": entity_id}
                            for col, val in updates.items():
                                set_clauses.append(f"{col} = :{col}")
                                values[col] = val
                            set_clauses.append("updated_at = NOW()")
                            set_clauses.append("enriched_at = NOW()")
                            update_sql = text(
                                f"UPDATE entities SET {', '.join(set_clauses)} WHERE id = :id"
                            )
                            await db.execute(update_sql, values)
                    else:
                        # All fields already filled — mark as enriched
                        skipped_count += 1
                        if not dry_run:
                            mark_sql = text(
                                "UPDATE entities SET enriched_at = NOW() WHERE id = :id AND enriched_at IS NULL"
                            )
                            await db.execute(mark_sql, {"id": entity_id})

                # Commit batch
                if not dry_run:
                    await db.commit()

                # Progress callback
                if progress_callback:
                    pct = (processed / total_count) * 100 if total_count else 100
                    await progress_callback(
                        pct,
                        f"Processed {processed}/{total_count} "
                        f"(enriched: {enriched_count}, skipped: {skipped_count}, not_found: {not_found_count})",
                    )

                # Rate limit: pause between DB batches
                await asyncio.sleep(1)

        action = "Would enrich" if dry_run else "Enriched"
        return ScriptResult(
            success=True,
            message=(
                f"{action} {enriched_count}/{processed} OSM entities "
                f"(skipped: {skipped_count}, not_found: {not_found_count})"
            ),
            affected_count=enriched_count,
            details=[],
        )
