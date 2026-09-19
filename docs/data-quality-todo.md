# Data Quality TODO — my3pai Unified Place Service

> Audit date: 2026-09-18 · Scope: all 1,952,124 active entities in the restored prod copy (local dev DB, read-only audit).
> Method: aggregate coverage queries + random sampling. No writes performed for this audit.
> Status: `[ ]` open · `[~]` in progress · `[x]` done · `[!]` blocked

## Guardrails (apply to every item)

1. **Dry-run first** — every new script defaults `dry_run=true`; review counts, then run live.
2. **Backup before mutation** — `scripts/db-backup.sh --remote` (or local) before any bulk write.
3. **Soft deletes only** — never `DELETE`; set `is_active=false` and only after product sign-off (importers own source data).
4. **Batch + timeout** — keyset batches, `READ_COMMITTED`, `statement_timeout=120000` (follow `unify_place_types.py`).
5. **Cache invalidation** — bulk writes (>20 rows) use `dmo:*` wildcard purge; per AGENTS.md, invalidate before commit.
6. **Verification query in the PR** — each item lists its acceptance query below; paste before/after numbers.
7. **Environment notes** — migration `014_place_kind_mappings` is applied to local dev; throwaway test DB (`:5433`) still needs `alembic upgrade head`.

---

## Batch 1 — results (2026-09-18, local dev copy)

| # | Status | Rows | Verification |
|---|---|---|---|
| DQ-01 | [x] | 149,029 | `my3pai unified_category IS NULL` → 0 |
| DQ-02 | [x] | 264,355 | OTM `description<>'' AND summary empty` → 0; max summary 1000 |
| DQ-03 | [x] | 2,550 | swiss media-without-thumbnail → 0 (coverage 34% → 65%) |
| DQ-04 | [x] | 387,560 | address candidates → 0 (rexby/my3pai 0% → 99%) |
| DQ-05 | [x] | 1,528,618 | non-ISO country → 0; 1,392,870 → ISO2, 135,748 junk → NULL |
| DQ-06 | [x] | 495,424 | tourpedia 100%, swiss_dmo 31% |

**Deviations / notes**

- **Run order changed:** DQ-05 ran before DQ-04 so synthesized addresses use normalized ISO2 codes expanded to English names; that is why DQ-04 affected 387,560 rows instead of 512,934 (junk country components had been nulled).
- **DQ-05 provenance:** `attributes.country_raw` was **not** stored — updating the JSONB triggers maintenance of the GIN index (`idx_entities_attributes`) and roughly doubled per-row cost on 1.5M rows. Rollback source = the 10.0.2.3 backup + the deterministic mapping in `country_data.py`. Script now uses a temp mapping table + row-batched updates + `max_seconds` budget (runs in chunks; rerun until "0 remaining").
- **DQ-05 outcome detail:** OSM country coverage stays low (505,590 NULL) because most of its non-ISO values were junk (monument names such as `Bildstock`, `kříž`, `Wegkreuz`) — nulling them is correct. Three letter-pairs (`AB`, `XY`) remain; negligible.
- **DQ-06 acceptance revised:** swiss_dmo cannot reach >90% — only 2,549 route entities carry `sm_url`; tourpedia hit 100%.
- **DQ-01 note:** `stay` maps at place_type granularity, so camping/glamping land in `hotel`; a kind-level pass (e.g. via `secondary_types`) can refine later.
- **Test suite:** unchanged pre-existing failure set. Found a test isolation gap while verifying: `tests/test_admin_scripts_unify.py` inserts `test_unify_*` categories without cleanup → reruns hit `unified_categories_slug_key` duplicates. Worth adding to the conftest cleanup (not done here).

---

## Batch 1 — Quick wins (XS effort, no external APIs)

### [x] DQ-01 — Unify my3pai categories (149,029 rows)
- **Evidence:** my3pai `unified_category`/`unified_subcategory`/`unified_category_id` = 0% while rexby is 100%. Place types: `experience` 94.5k, `foodanddrink` 35.1k, `stay` 16.9k, `transportation` 1.4k, `guide` 790, `unknown` 359.
- **Action:** add 6 rows to `place_type_mappings` for `source='my3pai'`, then run existing `unify_place_types` (`source=my3pai`).
- **Acceptance:** `SELECT count(*) FROM entities WHERE is_active AND source='my3pai' AND unified_category IS NULL` → `0`.
- **Notes:** consider kind-based mapping from `secondary_types` later; place_type mapping is the 30-minute fix.

### [x] DQ-02 — Backfill OTM `summary` from `description` (264,355 rows)
- **Evidence:** 264,355 opentripmap rows have `description` (`description_format='html'`, mostly plain Wikipedia extracts, avg 483 chars) and `summary IS NULL`. Laravel candidate cards read `summary`.
- **Action:** new script `backfill_summary_from_description` (source param, exclude rows whose description contains markup beyond `<`, dry-run). Set `summary = description` (optionally truncated to 1,000 chars).
- **Acceptance:** `SELECT count(*) FROM entities WHERE is_active AND source='opentripmap' AND description <> '' AND (summary IS NULL OR summary='')` → `0`.
- **Notes:** only 45/264k descriptions contain any tag, so no HTML cleanup needed beyond existing API transform.

### [x] DQ-03 — swiss_dmo thumbnail from media (2,550 rows)
- **Evidence:** 2,550 swiss_dmo entities have active `media` rows but empty `thumbnail_url` (media exists only for this source; 13,020 rows total).
- **Action:** new script `backfill_thumbnail_from_media`: pick the first active media by `sort_order, id` where `media_type='image'`, set `thumbnail_url`.
- **Acceptance:** count of swiss_dmo entities with active media and empty thumbnail → `0`.

### [x] DQ-04 — Synthesize missing `address` (512,934 candidates)
- **Evidence:** address missing but `locality`/`region`/`country` present: rexby 146,936 · my3pai 146,923 · osm 131,367 · tourpedia 64,880 · opentripmap 14,396 · swiss_dmo 8,177 · dzt 255. (Another ~263k rows have no location parts at all — out of scope.)
- **Action:** new script `heal_missing_address`: `address = concat_ws(', ', NULLIF(locality,''), NULLIF(region,''), NULLIF(country,''))`, only when `address` empty and at least one part exists. Respect a `source` filter.
- **Acceptance:** targeted sources' "missing address with parts" count → `0`.
- **Notes:** keep the raw parts column-based; do not overwrite existing addresses (including wrapped ones — only 13 remain).

### [x] DQ-05 — Normalize `country` to ISO 3166-1 alpha-2 (1,528,618 rows)
- **Evidence:** only 46,494 rows are ISO2 (`swiss_dmo` is the only clean source). Non-ISO examples: `United Kingdom` 223k, `Deutschland` 57k, `日本 (Japan)` 38k, `España` 32k, `Ελλάδα` 8.8k; `dzt` uses `n.v.` 6,353×. 121,720 distinct values, ~400k-row junk tail (place names). Domo `/search?country=` is exact-match, so `country=CH` misses ~99% of the corpus today.
- **Action:** new script `normalize_countries`:
  1. Curated variant→ISO2 mapping (top ~300 names incl. native forms, `Name (Native)` extraction, ISO2/ISO3 passthrough).
  2. Values not resolvable to a known country → set `NULL` (never guess), counted in dry-run details.
  3. Store the original in `attributes.country_raw` before overwriting (audit trail), only once.
- **Acceptance:** `SELECT count(*) FILTER (WHERE country !~ '^[A-Z]{2}$' AND country <> '')` → `0`; spot-check 50 random rows per source.
- **Notes:** `unaccent` extension is not installed; implement normalization in Python. Coordinate with importers so new data is normalized on write.

### [x] DQ-06 — `source_url` backfill (tourpedia 493k, swiss_dmo 8.2k)
- **Evidence:** tourpedia/swiss_dmo `source_url` = 0%, but tourpedia has `attributes.tourpedia_dbpedia_location` + `tourpedia_external_links`, swiss_dmo has `attributes.sm_url`.
- **Action:** extend a small backfill script (or reuse `extract_attributes`) to set `source_url` from those attributes where empty.
- **Acceptance:** >90% of tourpedia/swiss_dmo rows have `source_url`.

---

## Batch 2 — External enrichment (network-bound, existing/new scripts)

### [x] DQ-07 — Extend `enrich_from_wikidata` to accept `otm_wikidata` (426,577 QIDs)
- **Result:** all **426,577** OTM QID entities processed (finished 06:17). Coverage moved: summary 0→71%, description 44→71%, thumbnail 56→59%, website 0→9% (5,795 phones, 4,586 emails). Code extended with `source` param, ISO2 country resolution, redirects, Retry-After backoff, abort-on-fetch-failure.

### [x] DQ-08 — Run existing enrichment for OSM (148,530 QIDs / 45,412 wikipedia / 61,981 commons)
- **Result:** all eligible OSM processed; **190,076** OSM rows now have `enriched_at`. The 33,882-entity tail had no QIDs — 5,033 had only `osm_wikipedia` refs and 28,849 only Commons `File:` refs. Added a direct path: Wikipedia REST extract by `lang:Title` + Commons `Special:FilePath` thumbnails; first tail chunks yielded ~270 enriched / 500. OSM final: thumbnail 27→30%, phone 115.7k, email 64.3k (mostly pre-existing tag data).

### [x] DQ-09 — OTM classifications from kinds (600,074 rows)
- **Evidence:** 600,074 opentripmap entities have zero `classifications` rows while `secondary_types` carries 251 kind tokens (already parsed). `/classifications` is only populated for swiss_dmo/rexby/tourpedia.
- **Action:** small script mapping kind groups → classification `category`/`value_code` (e.g. `otm_kind` / `<kind>`), dedup per entity, soft-delete-safe.
- **Acceptance:** OTM entities with ≥1 active classification ≈ 100%; spot-check 3 entity details.
- **Notes:** confirm with API consumers whether classifications are surfaced; otherwise leave in backlog.

---

## Batch 3 — Scoring, coordinates, retention

### [x] DQ-10 — Generalize `quality_score` to all sources (~1.4M rows)
- **Result:** new script `score_entities` (supersedes `score_osm_entities`) wrote scores for **1,952,124 / 1,952,124 active entities** (1,937,636 rows changed) in ~69 min. Formula: source-agnostic completeness 0–100 (text 23 · contacts/opening/price 21 · name 2 · visual 12 · location 18 · categorization 10 · engagement 8 · attribute richness 6); idempotent, batched, 2% page-sample dry-run.
- **Final distribution:** rexby 60.7 · my3pai 58.4 · opentripmap 47.3 · tourpedia 44.1 · swiss_dmo 43.7 · dzt 33.7 · osm 29.4 (p90: rexby 74, my3pai 71, swiss 66, otm 64, osm 39). Samples: top = fully populated rows (max 80); bottom = bare `Unnamed …` OSM rows (min 15).

### [ ] DQ-11 — Coordinate backfill for rexby/my3pai (221,097 rows) — prerequisite for DQ-12
- **Evidence:** rexby 110,554 / my3pai 110,543 lack `latitude`/`longitude`/`location` (74%). No coordinate attributes exist in their JSONB, so existing `heal_missing_coordinates` cannot help.
- **Action:** external geocoding pipeline (address/locality/region or name + region) via a provider with caching; write both `latitude`/`longitude` and `location` (PostGIS update pattern). Start with the ~110k my3pai rows (they have `region` 98%, `locality` 97%).
- **Acceptance:** dry-run shows % resolvable; sample manually validated; spatial endpoints return these entities.
- **Notes:** biggest map-visibility gap. Budget API cost + rate limits; keep a failure list for retry.

### [ ] DQ-12 — Remove (soft-delete) records with no lat/lon — **decision gate**
- **Requested:** drop records without coordinate data so they cannot pollute search/consumer flows.
- **Exact scope:** rexby 110,554 · my3pai 110,543 · swiss_dmo 490 = **221,587 entities (11.3%)**. 110,273 rexby and all 110,543 my3pai have text content; swiss_dmo only 20.
- **Key nuance:** spatial endpoints already exclude them (no `location` → no `ST_DWithin`/bbox match). They currently appear only in text search/detail/categories. Removal therefore affects search recall and detail 404s, not map results.
- **Decision required (product + importer owner):**
  1. **Preferred:** complete DQ-11 first, then only soft-delete what remains unresolvable.
  2. Confirm scope (all 3 sources vs rexby+my3pai only; swiss_dmo has only 490).
  3. Alternative to removal: add an explicit `has_location` filter to `/search` so map-driven clients never receive unmappable results while text/detail consumers keep them.
- **Action if approved:** new admin script `deactivate_missing_coordinates` — dry-run default, `source` param, batch keyset (`WHERE latitude IS NULL AND longitude IS NULL AND is_active`), `is_active=false`, cache invalidation, metrics; commit per batch.
- **Acceptance:** active count for targeted sources drops to 0; all read endpoints exclude them; rollback documented (`UPDATE entities SET is_active=TRUE WHERE ...`).
- **Rollback:** single UPDATE by id list exported before deactivation; take a backup first.
- **Risks:** nearly 220k enriched records vanish for search users; importers may re-add unmappable rows on next sync (fix upstream or keep the script in the runbook); soft-delete is reversible only while the export list/backup exists.

---

## Batch 2 — results (completed 2026-09-19 12:37)

| # | Status | Outcome |
|---|---|---|
| DQ-07 | [x] | All **426,577** OTM QID entities processed (finished 06:17). Coverage: summary 0→71%, description 44→71%, thumbnail 56→59%, website 0→9%, plus 5,795 phones and 4,586 emails. |
| DQ-08 | [x] | All eligible OSM processed; **190,076** rows have `enriched_at`. Tail (33,882 no-QID entities) handled via a new direct Wikipedia/Commons path. Final thumbnail 30%, phone 115.7k / email 64.3k. |
| DQ-09 | [x] | 1,438,359 `kind` classification rows for 591,967 OTM entities (98.6%) in 117s; idempotent. |

**Enrichment findings (2026-09-18/19):**
- **Root cause of 429s = User-Agent policy**, not IP/VPN: `DMO-Enricher/1.0` → HTTP 429; `my3pai-dmo-enricher/1.0 (https://my3p.ai)` → HTTP 200. Default UA updated in both the parameter and the `run()` fallback.
- **Throughput is payload-bound**: a 50-QID fetch with `claims|descriptions|labels|sitelinks` is ~1.4 MB / 7 s; batches are chunked 50 QIDs at a time. OTM took ~10.5 h end-to-end.
- **VPN instability**: long-lived connections stall (no timeout, no error) and macOS denies sockets to the old process after a switch. Solved with a PID-tracked supervisor that restarts dead/stalled workers within ~5–10 min and chains OTM → OSM automatically; `enrich_description` enabled only for the OSM tail.
- **Property-ID bug found & fixed:** the script mapped phone/email/address/hours to wrong Wikidata properties (`P426` aircraft registration, `P479` input device, `P682` biological process, `P1412` languages). Non-string claim values then crashed a batch write and the retry loop stalled the worker. Fixed to `P1329` (phone), `P968` (email), `P6375` (address); opening hours removed (no single property). Added a `_set_field` guard that skips non-string values. No junk was committed before the fix.
- **OSM tail (no-QID entities) handled:** 5,033 had only an `osm_wikipedia` `lang:Title` ref and 28,849 only Commons `File:` refs, and the script could not advance on them (infinite re-fetch loop). Added a direct path: Wikipedia REST extracts by `lang:Title` (description + first-sentence summary) and Commons `Special:FilePath` thumbnails; ~270 enriched per 500-entity chunk.
- A bug that marked entities as not-found when a fetch failed was fixed; 290 wrongly-marked OSM rows were reset to `enriched_at = NULL`.

**Final coverage (enriched sources):**

| source | n | summary | descr | thumb | website | country | phone | email |
|---|---|---|---|---|---|---|---|---|
| opentripmap | 600,074 | 71% | 71% | 59% | 9% | 100% | 5,795 | 4,586 |
| osm | 543,032 | 40% | 38% | 30% | 42% | 7% | 115,705 | 64,322 |

**Optional follow-up:** a long `enrich_description=true` pass for OTM to replace Wikidata one-liners with full Wikipedia prose (days; run nightly if detail-page richness becomes a priority).

---

## Backlog (not easy / needs product input)

- [ ] **DQ-13 — Cross-source duplicate linking:** 43,380 normalized names appear in ≥2 sources with coordinates. Needs matching (name + proximity + source trust), canonical-entity choice, and a review UI.
- [ ] **DQ-14 — swiss_dmo route attributes → `routes` table:** 2,550 entities carry `distance_km`, `ascent_m`, `descent_m`, `duration_min`, `geojson`, `sm_url`; `routes` table is empty and has no API endpoints.
- [ ] **DQ-15 — `enriched_at` / provenance:** set consistently for all enrichment scripts so re-runs can skip already-processed rows.

## Verification queries (paste with PRs)

```sql
-- DQ-01
SELECT count(*) FROM entities WHERE is_active AND source='my3pai' AND unified_category IS NULL;

-- DQ-02
SELECT count(*) FROM entities WHERE is_active AND source='opentripmap'
  AND description <> '' AND (summary IS NULL OR summary='');

-- DQ-03
SELECT count(DISTINCT e.id) FROM entities e
  JOIN media m ON m.entity_id=e.id AND m.is_active
 WHERE e.is_active AND e.source='swiss_dmo' AND (e.thumbnail_url IS NULL OR e.thumbnail_url='');

-- DQ-05
SELECT count(*) FILTER (WHERE country <> '' AND country !~ '^[A-Z]{2}$') AS non_iso,
       count(DISTINCT country) AS distinct_countries
  FROM entities WHERE is_active;

-- DQ-12
SELECT source, count(*) FROM entities
 WHERE is_active AND (latitude IS NULL OR longitude IS NULL) GROUP BY 1;
```

## Suggested sequencing

1. **Week 1:** DQ-01 → DQ-02 → DQ-03 → DQ-04 → DQ-06 (all local, dry-run + verify).
2. **Week 2:** DQ-05 (normalize, biggest single fix) and start DQ-08 scheduled enrichment.
3. **Week 3+:** DQ-07 (OTM enrichment), DQ-09, DQ-10; open DQ-12 decision with a DQ-11 estimate.
4. Backlog items reviewed monthly.

---

## Search ranking (Option 3a) — implemented 2026-09-19

- **Change:** `/search` with `q` now ranks by
  `similarity(name, q) [+ 0.5 · similarity(summary, q) when fulltext] + 0.1 · (quality_score / 100)`,
  tie-broken by `id`. Searches without `q` keep name ordering. Cursor is rank-based `(rank_score, id)`; legacy name-based cursors return `400 InvalidCursor` instead of failing.
- **Before → after (dev copy, top-5):**
  - `eiffel tower`: was `38 Eiffel`, `58 Tour Eiffel`, `Abjar Tower (AE)`… → now `Eiffel Tower [opentripmap] (FR)` ×5 (viewpoint/monument)
  - `Zermatt`: was `5-Seenweg Zermatt` → now the `Zermatt` region first, then Zermatt tours
  - `hotel` / `pizza` / `museum`: exact-name matches now first (was alphabetical lottery)
- **Performance:** GIN trigram index still used (Bitmap Index Scan); `eiffel tower` 340–460 ms, `museum` 474 ms, `hotel` ~3.2 s cold on 21.9k candidates (same order as before); API caches for 5 min.
- **Critical find & fix:** `opentripmap` was missing from `data_sources`, so the `source IN (enabled)` filter silently excluded **all 600k OTM entities** from `/search`, `/nearby` and `/map`. Fixed by migration **016** (registers any active source missing from `data_sources`); opentripmap is now enabled.
- **Caveat:** `quality_score` is source-skewed (rexby avg 60.7 vs osm 29.4), so the prominence weight is deliberately small (0.1, a tie-breaker). Revisit per-source normalization if ranking skew appears.
