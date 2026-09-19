# 20 Security / Performance / Quality Improvements

> Scope: full audit of `src/dmo/` (API, services, middleware, models, admin, admin_scripts), `config.py`, `db.py`, `Dockerfile`, `docker-compose*.yml`, `entrypoint.sh`, `migrations/`, `tests/`.
> Standard: non-nitpick only — each item is exploitable, causes outage/scale failure, corrupts data, or breaks observability/reliability.
> Evidence format: `file:line` verified against current tree.

Prioritization: P0 = fix now (auth / RCE / data exposure / crash), P1 = fix this quarter (scale / correctness / costly outage), P2 = harden next (defense-in-depth / operability).

---

## SECURITY (1–8)

### 1. [P0] Write-auth fails open when `API_KEY` is empty — make `verify_api_key` fail closed + constant-time compare
**Evidence:** `src/dmo/api/router.py:72-74`, `src/dmo/config.py:18`, `src/dmo/main.py:32-33`

```python
def verify_api_key(x_api_key: str = Depends(api_key_header)) -> None:
    if settings.api_key and x_api_key != settings.api_key:  # falsy api_key => all writes pass
```

`api_key` defaults to `""`. The only guard is the `lifespan` check, which is bypassed by any app import that doesn't run lifespan (tests, second worker import, refactor, ASGI sub-mount). `!=` also leaks via timing.

**Impact:** silent full bypass of all 8 `POST/PUT/DELETE` endpoints.

**Fix:**
- Inside `verify_api_key`: `if not settings.api_key: raise HTTPException(500, "server misconfigured")` (fail closed).
- Compare with `secrets.compare_digest(x_api_key or "", settings.api_key)`.
- Keep lifespan check as defense-in-depth, add startup test asserting 401 on writes with empty key.
- Effort: S (<1h).

### 2. [P0] Admin stored-XSS via Jinja2 `autoescape=False`
**Evidence:** `src/dmo/admin/router.py:36-38`, templates `entities/detail.html:4,6-8,53-69`, `taxonomy/browse.html:64,72-73`, `classifications/browse.html:41-43`

```python
_jinja_env = Environment(loader=FileSystemLoader(_templates_dir), cache_size=0)  # autoescape defaults False
```

Every `{{ }}` renders raw. Entity name/source/place_type/taxonomy names are write-API-controlled → stored XSS executes in the admin browser that holds Basic-auth credentials.

**Fix:** `Environment(..., autoescape=select_jinja_autoescape(['html','xml']))` (or `autoescape=True`). Audit templates for intentional `|safe`, replace with explicit allowlist. Add a test posting `<script>`/`<img onerror>` entity name and asserting escaped admin HTML.
- Effort: S.

### 3. [P0] ProseMirror serializer XSS + unsanitized `description_format` fallthrough
**Evidence:** `src/dmo/services/detail.py:42-45,65-66,76-77,113`, `src/dmo/models/schemas.py:167-168`

- `level = node.get("attrs", {}).get("level", 2)` → `f"<h{level}>"` — `level: '2 onclick=alert(1)'` breaks out.
- `_safe_href` blocks `javascript:/data:/vbscript:` but returns value **unescaped** into `<a href="{href}">` — `'" onmouseover="...'` breaks out.
- `transform_description` returns `description` verbatim for any unknown `description_format` — raw stored HTML/JS to all consumers.

**Fix:** coerce `level = max(1, min(6, int(...)))` with try/except fallback to 2; `html.escape(href, quote=True)`; `bleach.clean(...)` the fallthrough branch (never return raw). Add `test_xss` cases for all three vectors.
- Effort: S.

### 4. [P0] Postgres + Redis published to `0.0.0.0` with default secrets, Redis unauthenticated
**Evidence:** `docker-compose.yml:34,44`, `docker-compose.prod.yml:44,64`, `docker-compose.prod.yml:41 (`POSTGRES_PASSWORD:-changeme`), `:12-13` (`ADMIN_*:-admin`), `config.py:24-25`, `alembic.ini:3`

All composes bind `5432:5432` + `6379:6379`. Redis has no `requirepass`/`bind`/TLS — anyone reaching the host can poison `dmo:*` caches, wipe `ratelimit:*`, persist junk. DB + admin UI boot with guessable defaults.

**Fix:** remove host port publishing for DB/Redis in staging/prod (use internal networks + SSH tunnel for ops); set `requirepass` from generated secret, `bind 127.0.0.1`, TLS or private network; delete `:-changeme` / `:-admin` fallbacks so boot fails closed; rotate any exposed creds; add CI grep test forbidding `:-changeme`, `6379:6379` in prod compose.
- Effort: M (config + secret rotation).

### 5. [P1] `X-Forwarded-For` trusted by default + non-atomic, fail-open rate limiter with attacker-controlled keys
**Evidence:** `src/dmo/config.py:20`, `src/dmo/middleware/rate_limit.py:28-33,39-40,49-64,73-75`

Any direct client can send random `X-Forwarded-For` per request and never hit the limit, while the real IP is lost from logs. Check (`ZCARD`) and add (`ZADD`) are two pipelines (burst 2–10× over limit under concurrency). Any `RedisError` fails open silently. Key `ratelimit:{ip}` is attacker-controlled → unbounded key creation. `/health`, `/metrics`, docs bypass limiting entirely.

**Fix:** default `trust_proxy_headers=False`; when enabled, honor header only from `TRUSTED_PROXY_CIDRS` allowlist; normalize/validate IP (`ipaddress` module) before using as key; replace check-then-add with one Lua script (atomic sliding window); fail-closed-or-alert on Redis error (at minimum metric + log, coarse in-memory fallback); add coarse limit to exempt paths.
- Effort: M.

### 6. [P1] Admin SSRF via unvalidated `llm_endpoint` + plaintext secret + no CSRF
**Evidence:** `src/dmo/admin/router.py:556-567`, `src/dmo/admin/llm_client.py:44-48,56-57`, `src/dmo/admin/settings_manager.py:31-38`, `src/dmo/admin/auth.py:14-22`

`llm_endpoint`/`llm_api_key` saved with zero URL validation, then `POST {endpoint}/chat/completions` with `Authorization: Bearer <key>`, `timeout=60`, redirects followed. Points at `http://169.254.169.254/`, internal DB/Redis/admin ports → SSRF + secret leak. Key stored plaintext in `/data/admin_settings.json`. All mutating admin routes rely on browser-attached Basic auth with no CSRF token / Origin check / SameSite semantics → cross-site POST can run scripts, mutate taxonomy/mappings.

**Fix:** allowlist `https` (http only for explicit loopback/test flag), deny private/link-local/metadata (`169.254.169.254`, `10/8`, `172.16/12`, `192.168/16`, `127/8`, `::1`) with redirect-chain revalidation, `follow_redirects=False`, short timeout; store secret with `0600` perms + redaction on read (`********` unless rotated); add CSRF tokens (or `Origin`/`Referer` check + `SameSite`) to all `POST/PUT/DELETE /admin/*`.
- Effort: M–L.

### 7. [P1] CORS `*` by default, credentials miscombined, docs always on, no security headers
**Evidence:** `src/dmo/config.py:15`, `src/dmo/main.py:56-57,76-91`

`allowed_origins="*"` + `allow_methods/headers=["*"]` lets any site read public endpoints cross-origin. Non-wildcard branch sets `allow_credentials=True` with `["*"]` methods/headers (browsers reject/over-permit). `/docs` + `/redoc` unconditionally expose full schema including write shapes. No `HSTS`, `CSP` (critical for HTMX admin), `X-Content-Type-Options`, `frame-ancestors` (admin clickjackable), `Referrer-Policy`.

**Fix:** default to explicit origin list (empty = same-origin only); never combine `*` with credentials; enumerate methods/headers; gate docs behind `ENABLE_DOCS` (off in prod); add headers middleware (`Strict-Transport-Security`, `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `frame-ancestors 'self'`, `Referrer-Policy`).
- Effort: S–M.

### 8. [P1] Bulk upsert is an asymmetric-DoS primitive — no body/attributes caps
**Evidence:** `src/dmo/api/router.py:453-460`, `src/dmo/models/schemas.py:167,212`, `src/dmo/services/write.py:245-468`, `Dockerfile:31`

Up to 1000 entities/req × 50 KB `description` + unbounded `attributes: dict` ≈ 50 MB+ JSON per request, then row-by-row ORM + `_set_locations_batch`, × 8 workers. No request-body size cap, no `attributes` depth/keys/bytes bound.

**Fix:** reverse-proxy + app body cap (e.g. 1–2 MB, `Content-Length` + streaming guard); bound `attributes` (e.g. ≤50 keys, ≤32 KB serialized, depth ≤4, validated in Pydantic); lower batch ceiling (e.g. 200) or queue bulk work; return 413 with envelope; load-test worst case.
- Effort: M.

---

## PERFORMANCE (9–14)

### 9. [P0] Broken distributed lock: `hash(source)` is per-process random → no cross-worker serialization
**Evidence:** `src/dmo/services/write.py:278,281-283,401-403`

```python
lock_id = hash(source) % (2**31)  # PYTHONHASHSEED-randomized per worker
await session.execute(text("SELECT pg_advisory_xact_lock(:lock_id)").bindparams(lock_id=lock_id))
```

Different uvicorn workers compute different lock IDs for the same source → concurrent `bulk_upsert` bypass serialization → the `IntegrityError` retry path becomes the hot path under load.

**Fix:** stable ID, e.g. `zlib.crc32(source.encode()) & 0x7FFFFFFF` or `int.from_bytes(hashlib.sha256(source.encode()).digest()[:4], 'big') & 0x7FFFFFFF`. Add concurrency test asserting same lock ID across processes.
- Effort: S.

### 10. [P0] Pool sized to exceed Postgres + per-request `SET` tax + `REPEATABLE_READ` on reads
**Evidence:** `src/dmo/config.py:12-13`, `src/dmo/db.py:18-26,42-51`, `docker-compose.prod.yml:6,24-26`

`pool_size=10 + max_overflow=5` = 15/worker × 8 workers = 120 conns vs Postgres default `max_connections=100` on a 4-CPU/2 GB API container → refused/timeout spikes. Every `get_session()` pays an extra `SELECT set_config('statement_timeout',…)` RTT before row 1. `REPEATABLE_READ` on read-only list/detail traffic → heavier snapshots, more serialization aborts during bulk upserts.

**Fix:** size pool to `(max_connections / workers)` with headroom (e.g. 5+5 for 8 workers against 100, or raise PG `max_connections` deliberately); set `statement_timeout` via `connect_args.server_settings` (no extra RTT); scope `REPEATABLE_READ` to write engine/session only, `READ COMMITTED` for reads.
- Effort: M.

### 11. [P1] `COUNT(*) OVER()` on every page + `SELECT entities.*` shipping TOAST columns
**Evidence:** `src/dmo/services/search.py:75-83,76,99-101`, `src/dmo/services/spatial.py:69-79,176-185`, `src/dmo/api/router.py:85,138,190,273`, `src/dmo/models/database.py:44-45,85,90-94,113`, `src/dmo/models/schemas.py:69-91`

Every 20-row page (up to 100) computes exact total over the full match set — O(matches)+sort for 500 km `nearby`, planet-size `map` bbox, trigram scans — just to fill `total`. Simultaneously `SELECT entities.*` transfers `description/summary/opening_hours/attributes` + 4×2 KB URL columns that `EntityListItem` never needs, inflating DB→app, Pydantic validation, `json.dumps`, and 1 GB `allkeys-lru` Redis values.

**Fix:** explicit column list matching `EntityListItem`; add `?include_total=false` fast path for map/nearby (or separate longer-TTL count cache / approximate count); cap radius/bbox area allowed with exact counts.
- Effort: M.

### 12. [P1] Hot filter/sort paths have no supporting index
**Evidence:** `src/dmo/models/database.py:127-148,181-184,203-206`, `src/dmo/services/search.py:53-55,81`, `src/dmo/services/spatial.py:51-53,160-162`, `src/dmo/services/classifications.py:67`, `src/dmo/services/detail.py:165`, `src/dmo/services/taxonomy.py:36-54`, `src/dmo/admin/router.py:171`

- Leaf filter `unified_subcategory = :uscat` has no index (only `idx_entity_unified_category`).
- `classifications ORDER BY category, value_code, id` has no composite index.
- Cursor `ORDER BY entities.name, id` has no B-tree (trigram GIN accelerates `%`, not ordering).
- Detail children `ORDER BY sort_order` has no `(entity_id, sort_order)` index.
- `taxonomy (parent_id, is_active, sort_order)` has no composite; admin `ILIKE %q%` ignores trigram index.

**Fix (migration):** add `(unified_subcategory, is_active)`, `(category, value_code, id) WHERE is_active`, `(is_active, name, id)`, `media(entity_id, sort_order) WHERE is_active`, `unified_categories(parent_id, is_active)`; use `CREATE INDEX CONCURRENTLY` + `IF NOT EXISTS`; `EXPLAIN (ANALYZE, BUFFERS)` before/after on leaf-filter, classifications, cursor-page queries.
- Effort: M.

### 13. [P1] `nearby` computes `ST_Distance` 3×/row, sorts without KNN; taxonomy counts in Python
**Evidence:** `src/dmo/services/spatial.py:60-61,69-78`, `src/dmo/services/taxonomy.py:63-81,92`

`dist_expr` appears twice in cursor filter + once in SELECT + `ORDER BY distance_km` — geography math 3×/candidate + full sort, no `<->` KNN. Float-equality cursor (`= :cursor_distance`) is unstable. `list_categories` loads all matching IDs with `.all()` twice (one query's result recomputed as `sum()` later) — MBs of network + Python CPU per cold `unified-categories` call at 10⁵–10⁶ rows.

**Fix:** CTE computing distance once, order/paginate on CTE output; prefer KNN `<->` for ordered path; cursor on stable `(distance, id)` with epsilon/lexicographic comparison; replace Python counting with single `GROUP BY unified_category_id` (verify one query serves both top + leaf counts).
- Effort: M.

### 14. [P1] Cache invalidation is sequential `SCAN`+`DELETE`, misses a pattern, wipes unrelated keys
**Evidence:** `src/dmo/services/cache.py:64-68`, `src/dmo/services/write.py:28-39,455-460`, `src/dmo/api/router.py:318-319`, `src/dmo/admin/script_runner.py:14-15`

`async for key in scan_iter: await client.delete(key)` = N RTTs with blocking `DEL`. Single-entity write does 7 sequential SCANs; bulk ≤20 fans out to 140 SCANs; media/classification writes nuke all search/nearby/map caches though entity row unchanged. Pattern list omits `dmo:unified_categories:*` while endpoint caches under `unified_categories` → stale taxonomy until TTL. Bulk `>20` uses `dmo:*` which also wipes `dmo:script_run:*`, breaking admin poll.

**Fix:** pipeline + `UNLINK` (non-blocking); add missing `dmo:unified_categories:*`; namespace script runs outside `dmo:` (e.g. `dmo_admin:script_run:*`) or exclude from bulk wipe; targeted `DEL detail/open_status` keys for single-entity writes, reserve SCAN for large bulks; measure invalidation p95 before/after.
- Effort: M.

---

## RELIABILITY / QUALITY (15–20)

### 15. [P0] Prometheus cardinality explosion + unlabeled cache counters + public `/metrics`
**Evidence:** `src/dmo/main.py:98-109`, `src/dmo/metrics.py:15-16`, `src/dmo/api/metrics.py:7-9`, `src/dmo/middleware/rate_limit.py:39`

`endpoint=request.url.path` creates unbounded series per `/{source}/{source_id}` — classic Prometheus OOM. `cache_hits/misses_total` have no labels, contradicting "per endpoint" spec. `/metrics` is unauthenticated, rate-limit-exempt, returns tuple instead of `Response` (corrupts OpenAPI).

**Fix:** label by `route.path` template (or `route.name`); add `endpoint` label to cache counters; protect `/metrics` (internal network + optional bearer, never public); return proper `Response`; add cardinality CI test.
- Effort: S–M.

### 16. [P1] Error envelope inconsistent; domain/DB errors unmapped and unlogged
**Evidence:** `src/dmo/exceptions.py:17-98,101-111`, `src/dmo/services/write.py:471-503,523-544`, `tests/test_errors.py:82-86,108-112,242-248`

No `RequestValidationError` handler → 422s return `{"detail":…}` not `{error,message,code,request_id}`. `code` is int for `HTTPException`, string for `AppError`, inconsistent `_error_type` (405/504 → `"Error"`). `AppError` never logged. Only `57014` mapped; `IntegrityError/DBAPIError/OperationalError/CancelledError` → generic 500. `create_media/classification` duplicate-key → 500 not 409.

**Fix:** add `RequestValidationError` handler emitting envelope; normalize `code` type (document int-vs-string, fix tests); complete `_error_type`; log `AppError` at warning with `request_id`; map `IntegrityError`→409, `OperationalError/CancelledError`→503/504; add contract tests for envelope on 400/404/422/504.
- Effort: M.

### 17. [P1] `asyncio.gather` shares one `AsyncSession` + 3× redundant entity lookup on detail
**Evidence:** `src/dmo/services/detail.py:131,135-139,157-186,198-207`

`session.exec(entity_stmt)` runs concurrently with two helpers on the **same** `AsyncSession` (documented non-concurrency-safe) → `PendingRollbackError`/corruption risk under load, still pins one pooled conn. Both helpers `JOIN Entity WHERE source/source_id` — same PK lookup 3×, fetching/discarding `Entity` columns.

**Fix:** fetch entity first (`SELECT id …`), then `WHERE entity_id = :id` for media/classifications (sequential or independent sessions); drop joins; add load test asserting no `PendingRollbackError` under concurrent detail.
- Effort: S–M.

### 18. [P1] Stampede guard hammers Redis then double-fetches; cache-hit path has no HTTP caching
**Evidence:** `src/dmo/services/cache.py:98-111,113-124`, `src/dmo/api/router.py:117-123,169-177,233-239,293-303,318-326,350-358`, `src/dmo/middleware/rate_limit.py:49-64`

Losers poll `GET` every 50 ms × 100 = 100 GETs/waiter/hot-key miss; on timeout every router fallback **re-executes `_fetch_*` outside the lock** — the herd the lock was meant to stop — plus fire-and-forget set. Rate limiter adds 6 Redis ops/req even on cache hits (Redis does more work limiting than serving). Cache hits still pay `json.loads` + full Pydantic revalidation with no `Cache-Control`/`ETag` → no CDN/browser offload.

**Fix:** jittered exponential backoff + single-flight (`BLPOP`/pub-sub) or short negative cache; don't re-fetch when lock path already fetched; collapse limiter to one Lua pipeline; serve cached bytes with `ETag`/`Cache-Control`, validate-on-write instead of validate-on-hit.
- Effort: M–L.

### 19. [P1] Background-task and shutdown hazards: use-after-close session, fire-and-forget loss, fragile entrypoint
**Evidence:** `src/dmo/admin/router.py:438-493,531-543,944-981`, `src/dmo/services/cache.py:133-141`, `src/dmo/main.py:29-48`, `entrypoint.sh:1-8`, `src/dmo/admin/script_runner.py:72-73`, `src/dmo/middleware/request_id.py:20`, `src/dmo/logging.py:19-22`

Admin captures request-scoped `session: Depends(get_session)` inside `asyncio.create_task(run_task())` — generator closes on response, task uses dead connection. Global `./.stop` file in CWD has no `run_id` isolation/cleanup. `cache_set_async` tasks can die on shutdown (`_cache_task_done` only logs). Lifespan closes cache/engine but doesn't drain workers/scripts. `entrypoint.sh` has no DB retry/signal trap; `alembic.ini` hardcodes `postgres:postgres@localhost`; `db.py` passes `None` if env missing. `bind_contextvars(request_id)` never cleared → stale ID leaks across keep-alive. Health `wait_for(...1.5s)` wraps `session.exec` but not the `get_session` 10 s `set_config` outside it → `/health` can block ~10 s; no `no-store`/`Retry-After` on 503; `wait_for` cancellation can leak pooled conns (also `main.py:122-137` 30 s timeout doesn't abort PG statement; long admin scripts share same budget).

**Fix:** open new `async_session()` inside background tasks (never capture request session); per-run cancel tokens (not global file); shield + await or durably queue cache writes; `--graceful-timeout` + connection draining + script cancellation; entrypoint retry loop + honor `DATABASE_URL_SYNC`; `clear_contextvars()`/token reset in middleware; scope health timeout to include session setup, add `Cache-Control: no-store`; ensure session close on `CancelledError`.
- Effort: M–L.

### 20. [P2] Shallow health, unsafe migrations, OpenAPI drift, flaky harness, dead code
**Evidence:** `src/dmo/api/health.py:17-42`, `migrations/env.py:7`, `migrations/versions/001_initial_schema.py:85-94,138,141-147`, `migrations/versions/013_add_data_sources.py:27-33`, `scripts/export-openapi.py:10-15`, `src/dmo/api/router.py:124-126,332-385`, `tests/conftest.py:33-89,107-145`, `tests/test_concurrency.py:44-47,67-70,95-99`, `run_phase2.py:13`, `scripts/rephrase.py:98-143`, `src/dmo/admin/llm_client.py:12-72`, `src/dmo/services/cache.py:40-61`, `loadtest/`, `results/`

- Health: only `SELECT 1` + `ping`, no `alembic_version`/pool/migration check, catches `TimeoutError` not `CancelledError`.
- Migrations: `target_metadata=None` (autogenerate empty), non-concurrent GIST/GIN (write-blocking), no `IF NOT EXISTS`, unconditional extension drop on downgrade, unguarded seed insert, no up/down test.
- OpenAPI: built Jul 2 manually, no CI freshness check; `JSONResponse` returns bypass `response_model` validation → spec can lie.
- Tests: `conftest` monkeypatches by string match, shares one `AsyncSession` across `asyncio.gather` (unsafe) + function-scoped `DELETE` races; no coverage for `/metrics`, fail-open, stampede-timeout.
- Dead: `run_phase2.py`, `scripts/rephrase.GenericLLM` duplicating `LLMClient`, `script_runner.clean_old_runs` stub, unused `cache_get/cache_set`, committed `loadtest/` + `results/*.json/.log`.

**Fix:** deepen health (version + pool stats + `no-store`); `CONCURRENTLY` + `IF NOT EXISTS` + advisory-locked seeds + migration chain test; CI `export-openapi --check` + response-model validation tests; fix harness (`monkeypatch` fixture, per-task sessions, isolated cleanup; add missing-path tests); delete/dedupe dead code, gitignore artifacts; add `mypy`/`ruff-S` gate (`pyproject.toml:36-51` currently no `S/ANN/RUF`, no type gate).
- Effort: M (split into 2–3 PRs).

---

## Suggested execution order

1. P0 auth/data/XSS/lock/pool/metrics (1, 2, 3, 4, 9, 10, 15) — each <1 day, prevents breach/outage.
2. P1 scale/correctness (5, 11, 12, 13, 14, 16, 17, 18) — measure with `EXPLAIN (ANALYZE, BUFFERS)` + load test before/after.
3. Harden/operability (6, 7, 8, 19, 20) — SSRF/CSRF/CORS/DoS caps, shutdown, health, migration + OpenAPI CI.

## Verification checklist for each PR

- [ ] Repro/test demonstrating before/after (XSS payload blocked, 401 with empty key, stable lock ID across workers, `EXPLAIN` index hit, no `PendingRollbackError`, envelope shape asserted).
- [ ] `TEST_DB_URL=postgresql+asyncpg://postgres:changeme@10.0.1.8:5432/dmo TEST_REDIS_URL=redis://10.0.1.8:6379 uv run pytest tests/`
- [ ] `uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/`
- [ ] Regenerate OpenAPI if routers/schemas touched: `uv run python scripts/export-openapi.py`

---

## Appendix A — Live verification (2026-09-18, local PostGIS docker + local Redis)

Test baseline: `uv run pytest tests/ -q` → **294 passed, 12 failed** (rerun variance 291 passed / 15 failed + 2 errors — same pre-existing sets, see below). DB migrated via `alembic upgrade head`. Failures are pre-existing code/test drift, not caused by this doc:

- `test_disabled_sources` (5): expects `NOT IN` but `source_filter.py:88` returns `IN`-on-enabled; returns `""` when only disabled rows exist.
- `test_rate_limit_exceeded`: expects `raise HTTPException`, code returns `JSONResponse` 429 (`rate_limit.py:58`).
- `test_rephrase_*` (7) + `test_unify_dry_run`: LLM quality-gate `100% failures`, validation message drift.

Verdict per item — **all 20 REAL**, 2 with scope nuances:

| # | Item | Verdict | Live proof |
|---|------|---------|------------|
| 1 | fail-open API key | REAL | `settings.api_key=""; verify_api_key(None)` → `None`, no 401. Only `lifespan` guards. |
| 2 | Jinja autoescape off | REAL | `_jinja_env.autoescape is False`; `{{name}}` with `<script>` renders raw. |
| 3 | ProseMirror XSS trio | REAL | `_safe_href('" onmouseover=...')` unchanged; `level='2 onclick=...'` → `<h2 onclick=...>`; unknown format returns `<script>` raw. |
| 4 | 5432/6379 published | REAL | `"5432:5432"` + `"6379:6379"` in all 4 compose files. |
| 5 | rate-limit spoof/race/fail-open | REAL | `XFF: 1.2.3.4` wins over real IP; `except RedisError: call_next`; 2 separate pipelines, no Lua. |
| 6 | admin SSRF | REAL (authenticated) | No URL validation on save; `f"{endpoint}/chat/completions"` + `Bearer` key; gated by `verify_admin`. |
| 7 | CORS `*` + docs + no headers | REAL | `allow_origins=["*"]` default; `docs_url=/docs`; no CSP/HSTS/XCTO strings in `main.py`. |
| 8 | bulk DoS, no byte cap | REAL | `Body(max_length=1000)` counts items not bytes; `attributes={1000×1KB}` validates (~1 MB). |
| 9 | `hash(source)` lock | REAL | `PYTHONHASHSEED=0` → `2077156732` vs `=1` → `2016659491`. Fix: `zlib.crc32` stable. |
| 10 | pool 120 > PG 100 + SET tax + RR | REAL | `(10+5)×8=120`; `SHOW max_connections=100`; extra `SELECT set_config` per session; `REPEATABLE_READ` global. |
| 11 | `COUNT(*) OVER()` + `SELECT *` | REAL | Present in `search.py:75-77`, `spatial.py:69-72,176-178`; `EXPLAIN width=12497` (TOAST through sort/window). |
| 12 | missing indexes | REAL | `pg_indexes`: no `unified_subcategory`, no `(category,value_code)`, no `(name,id)` btree, no `(entity_id,sort_order)`. |
| 13 | 3× `ST_Distance` + Python counts | REAL (cursor-path only) | 1×/row first page, 3×/row with cursor (2 in WHERE + 1 SELECT); taxonomy 2× `.all()` + dict loop, first query dead. |
| 14 | SCAN+DELETE, missing pattern, `dmo:*` collision | REAL | `scan_iter→delete` per key; no `dmo:unified_categories:*` in `write.py:28-39` vs `router.py:319`; `dmo:script_run:*` matches `dmo:*`. |
| 15 | metrics cardinality + public `/metrics` | REAL | `endpoint=request.url.path`; cache counters label-less; `GET /metrics` → 200 unauth, rate-limit exempt. |
| 16 | envelope/DB-error mapping | REAL | No `RequestValidationError` handler (422 `detail` vs envelope); `_error_type(405/504)=="Error"`; `AppError` unlogged; non-57014 re-raised. |
| 17 | shared-session `gather` + 3× lookup | REAL | Same session into 3 coroutines; both helpers `JOIN Entity WHERE source/source_id`. |
| 18 | stampede poll + re-fetch, no HTTP cache | REAL | `100×50ms` poll then `return None,MISS`; all 7 endpoints re-`_fetch()`; 6 Redis ops/req; no ETag. |
| 19 | background/shutdown hazards | REAL | Request `session` in `create_task`; global `.stop`; fire-and-forget `cache_set_async`; `entrypoint.sh` single-attempt; `bind_contextvars` never cleared; health 1.5 s excludes 10 s `set_config`. |
| 20 | health/migrate/OpenAPI/harness/dead | REAL | `SELECT 1`+`ping` only; `target_metadata=None`; `export-openapi` diff +162 lines; `conftest` shared session; `clean_old_runs: pass`. |

No item was fake or no-impact. Downgrade candidates if key control is strict: #2 stays (upstream OSM/Wikidata + admin-taxonomy paths bypass API key), #6 stays P1 (requires admin creds but SSRF+plaintext secret+CSRF chain is real).

---

## Appendix B — Prod-scale re-verification (2026-09-18, 1,952,124 active entities)

Suite re-run against a throwaway migrated db (`dmo-test-verify` on :5433, never the dev copy): **294 passed, 12 failed — byte-identical failure set** (`disabled_sources` ×5, `rate_limit` ×1, `rephrase` ×6). No data-dependent failures; all pre-existing drift. The new `conftest.py` guard was live-tested against the dev copy and aborts (`1952414 rows > 10000 limit`), count unchanged after.

All probes below are read-only `SELECT` / `EXPLAIN (ANALYZE, BUFFERS)` on the restored dev db. Verdicts: items 11 (partially corrected), 12, 13 confirmed and upgraded with hard numbers.

| # | Probe on 1.95M rows | Result |
|---|---------------------|--------|
| 11 | Trigram search `name % 'hotel'`, page of 21 | **22,280 matches, 1.47–1.87 s, 90,812 buffers read + temp spill.** Bitmap goes lossy (33,322 lossy blocks, 416,449 recheck removals) → heap fetch dominates. **Correction to item 11:** with vs without `COUNT(*) OVER()` measured 1473 ms vs 1457 ms — the window itself adds ~1% here, not the bulk. The 1.5 s is trigram recheck + wide-row heap I/O + top-N sort. `include_total=false` still helps planet-scale map/nearby matches, but the bigger win on this path is lossy-bitmap pressure (raise `work_mem` / tighter index) + slim column list. |
| 11 | Row width, 100 active rows | **`SELECT *` = 163 kB vs slim list-item columns = 15 kB (~11×).** A 100-item page ships ~160 kB+ of TOAST through Pydantic + `json.dumps` + Redis per page. Confirmed, severity stands. |
| 12 | Leaf filter `unified_subcategory = 'museum'` | **Parallel Seq Scan over 1.95M rows (642k filtered/worker, 207,641 buffers read)** for a 21-row page. Prod `pg_indexes` confirms the gap — and prod already carries extra indexes (`idx_entities_country/type/rating/lat_lon/location_active_type`) yet still has none of: `(unified_subcategory)`, `(category,value_code)`, `(name,id)` btree, `media(entity_id,sort_order)`. **1.2M rows (62%) carry a subcategory**, so the leaf path is hot, not edge. Confirmed, P1→urgent. |
| 13 | Nearby Zurich 50 km, page of 21 | **8,994 matches, external merge sort spilling to Disk (3.9 MB) + temp files**, WindowAgg over full match set. Confirms distance-sort + exact-total cost on realistic radius. |
| 13 | Taxonomy counts | **`GROUP BY unified_category_id` over 1.95M rows: 744 ms, 34 rows out** — one query replaces the current two unbounded `SELECT id … .all()` + Python dict loop (MBs over the wire per cold call). Confirmed. |

**New data observation (not a new item):** 749,103 entities (38%) have empty `unified_category`, so top-level category filters silently miss over a third of the corpus. No code change proposed here — run the existing `unify_place_types` admin script and re-measure before treating category-filtered latency as purely an index problem.

---

## Appendix C — Consumer-driven triage (#10–#13), 2026-09-18

Consumer audited (read-only): Laravel bridge `../api` (`App\Services\UnifiedPlaceService`, `App\Http\Controllers\Public\UnifiedPlaceController`) and the only frontend, `website/src/components/map-creator-v2`. Findings change priorities for the read-path performance items:

**What the consumer actually does (measured against dev copy, service-layer timings):**

| Call path | Params | Freq / cache | Measured |
|---|---|---|---|
| `autocomplete` | `q`, limit 5, no cursor, `fulltext=false` | per keystroke (≥2 chars); Domo 5 min cache | `q=Zermatt`: 5 items, 231 ms, **81 KB**; `q=hotel`: 799 ms (21k matches), 3.4 KB |
| `nearby` candidates | radius **0.1 km**, page 100, top-level exclusions filtered in Laravel | per POI add | 6 rows, 77 ms, 9.9 KB |
| `nearby` DZT / Swiss | radius **10 km**, source, page 100 | map pan/zoom, 500 ms debounce, uncached | dzt: 127 total, 36 ms, 101 KB; **swiss_dmo: 104 total, 55 ms, 1.39 MB** |
| `nearby-services` | radius 1 km, page 1 + 10 | Laravel 5 min cache | 10 ms, 14.5 KB |
| `categories` | — | Laravel 1 h + Domo 5 min cache | cold only, rare |
| `detail` | — | per POI click, Domo 30 min cache | — |
| `search` cursor, `map`, `classifications` | — | **no callers found in website or bridge** | — |

**Payload attribution:** swiss_dmo page of 100 = **1.39 MB, of which 1.31 MB (95%) is `attributes`** (single entity "5-Seenweg Zermatt" = 79 KB attrs). The Laravel bridge/resources never read `attributes`; `summary` is used (`UnifiedPlaceEntityResource:33`), `attributes` is not. Removing/opt-out `attributes` from list responses → ~75 KB/page on this path (~18×).

**Triage verdicts:**

- **#11 — KEEP, re-scoped.** Real for payload, not for the count. `COUNT(*) OVER()` = 515 ms vs 460 ms for `q=hotel` (~12%, not the dominant cost); explicit column list does *not* change DB time (bitmap recheck dominates, 60k buffers). The win is transfer/serialization/memory: drop or opt-in `attributes` on list endpoints (`/search`, `/nearby`, `/map`); consider capping `attributes` on write. This also shrinks Redis values, gzip work, and Laravel JSON parsing.
- **#12 — SKIP for the current consumer.** No leaf slug is ever sent (all call sites pass top-level slugs or none; exclusions are filtered in Laravel, never forwarded). Search cursors unused (autocomplete can't paginate); top-N sort is 33 KB heap, not the bottleneck. `/classifications` and `/map` have no callers. Revisit only if server-side leaf filtering, map viewport queries, or search pagination are added.
- **#13 — SKIP for the current consumer.** The 3× `ST_Distance` path is cursor-only and no caller sends a cursor. Actual radii are 0.1/1/10 km → 10–77 ms, no disk spill (the 50 km Zurich probe was worst-case). Taxonomy Python counting runs at most once per hour (Laravel 1 h + Domo 5 min cache). Do it as cleanup if refactoring those files, not as a perf fix.
- **#10 — DEFER (hygiene).** One extra round-trip per request is negligible against observed 10–500 ms queries. Pool `15×8=120 > 100` is latent; current traffic is one Laravel app behind 300–500/min throttles, far below 100 concurrent queries. Worth fixing when adding workers, more consumers, or if `pg_stat_activity` shows waiting sessions.

**Consumer bug found (outside this repo, not fixed):** `UnifiedPlaceController::nearbyServices` sends `unified_category='transport'` and `'services'`; neither slug exists in the taxonomy (real top-level: `transportation`; `services` absent). `get_category_level` returns `None` → **no filter applied**, so "nearest transport" can be any POI. Same class: Laravel examples/tests use `attractions` (plural) while the DB slug is `attraction` — unknown slugs are silently ignored by Domo rather than rejected. Fix belongs in `../api` (real slugs, or ask Domo to 422 unknown filter slugs).
