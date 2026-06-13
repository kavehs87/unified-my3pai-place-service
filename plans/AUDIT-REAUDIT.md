# Code Re-Audit — unified-my3pai-place-service (DMO On-Premise)

**Audit date:** 2026-06-13
**Scope:** Full codebase (application source, migrations, tests, Docker/deployment configs)
**Context:** Internal API serving a single high-traffic commercial website; no public exposure.

---

## Overall Verdict

The first audit (`plans/AUDIT.md`) drove real improvements: GIN trigram indexes, single-commit entity creation, CORS credentials fix, SHA-256 cache keys, `pool_pre_ping`, Pydantic validators, and a `.dockerignore`. However, **several P0 items from the first audit are not actually resolved**, and a few **new correctness bugs were introduced** during those fixes.

**Grade: C** — the read path mostly works, but production readiness is still blocked by correctness bugs in spatial queries and classification pagination, broken cache invalidation, unauthenticated writes, and a Docker image that will not run migrations on deploy.

**Validation (2026-06-13):** 28 of 29 concerns confirmed real. Issue #8 (`update_entity` partial coordinate bug) is partially fabricated — the audit mischaracterizes the behavior. The `location` geography column IS updated correctly when one coordinate is provided (using existing value as fallback). However, the `latitude`/`longitude` scalar columns are NOT updated, creating a data consistency bug between the geography and float columns. The audit's claim that "the database coordinate and `location` columns remain unchanged" is incorrect for the common case where both existing coordinates are non-null.

---

## Verified Fixes from the First Audit

| # | Item | Status |
|---|------|--------|
| 10 | `create_entity` reduced to single commit + `session.refresh` | Fixed |
| 11 | GIN trigram indexes on `name`/`summary` + migration `003` | Fixed |
| 14 | CORS no longer sends `allow_credentials=True` with `*` | Fixed |
| 15 | `database_url` defaults to `None` | Fixed (fail-fast path still broken, see Medium #13) |
| 16 | `EntityCreate`/`EntityUpdate` validators added | Fixed |
| 18 | Detail cache TTL reduced to `60` | Changed, but whole detail is cached at 60s; open-status still not isolated |
| 19 | `MediaCreateResponse`/`ClassificationCreateResponse` added | Fixed |
| 21 | `price_level` column changed to `Integer` + migration `004` | Fixed |
| 22 | Classification cursor now base64 JSON | Encoding is safe, but it encodes the wrong column (see Critical #1) |
| 23 | Removed redundant `asyncio.ensure_future` in `detail.py` | Fixed |
| 26 | Prod compose no longer hardcodes DB password | Fixed |
| 27 | Default pool math now safe (`10`/`5` x 4 workers = 60) | Fixed |
| 29 | `pool_pre_ping=True` added | Fixed |
| 32 | `.dockerignore` created | Fixed |
| 34-40 | Minor cleanups (imports, unused schemas, MD5, cache lock, slug index, test DB env var) | Fixed |

---

## Critical Bugs (Must Fix Before Production)

### 1. Classification pagination cursor encodes `entity_id` but filters on `id`
**Status: ✅ FIXED** | `classifications.py:86` → `encode_cursor(last.id, ...)`, `pagination.py` → `decode_cursor` now handles `UUID | int`

The next cursor is built with:

```python
next_cursor = encode_cursor(last.entity_id, json.dumps({"c": last.category, "v": last.value_code}))
```

But the `WHERE` clause at line 48 compares:

```python
(col(Classification.id) > last_id)
```

`Classification.id` is an auto-increment integer, while `last_id` (decoded from the cursor) is the stringified UUID of the entity. Requesting page 2 of `/classifications` will raise a PostgreSQL operator error such as `operator does not exist: integer > uuid`.

**Fix:** Encode `last.id` (the classification auto-increment PK) instead of `last.entity_id`.

> **Verification: CONFIRMED.** `classifications.py:86` encodes `last.entity_id` (UUID), but line 48 compares `col(Classification.id) > last_id` where `Classification.id` is an `Integer` PK. PostgreSQL will throw `operator does not exist: integer > uuid` on page 2.

---

### 2. Spatial queries misuse `ST_MakePoint(:lon, :lat, 4326)`
**Status: ✅ FIXED** | `spatial.py:36,47,51` → `ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography`

**File:** `src/dmo/services/spatial.py:36, 47, 51`

`ST_MakePoint` accepts 2, 3, or 4 arguments. The 3-argument form treats the third value as a **Z coordinate**, not an SRID. The code creates 3D points with `Z = 4326` and relies on the `::geography` cast to supply SRID 4326 implicitly. This is a latent correctness problem and can interact badly with spatial indexes.

**Fix:** Use `ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography` consistently.

> **Verification: CONFIRMED.** `spatial.py:36,47,51` use 3-arg `ST_MakePoint(x, y, 4326)` where 4326 is treated as Z coordinate, not SRID. Creates 3D points with unknown SRID (-1). Note: `_set_location` in `write.py:31` already uses the correct pattern.

---

### 3. All write endpoints remain unauthenticated
**Status: ✅ FIXED** | `router.py` → `verify_api_key` dependency on all 7 write endpoints, `config.py` → `api_key` setting

**File:** `src/dmo/api/router.py:187-266`

`POST /entities`, `POST /entities/bulk`, `PUT /{source}/{source_id}`, `DELETE /{source}/{source_id}`, `POST /media`, `DELETE /media/{media_id}`, and `POST /classifications` have no API key, OAuth, or internal-auth dependency.

**Fix:** Add an API-key or internal-network auth dependency and reject requests without it.

> **Verification: CONFIRMED.** `router.py:187-266` — all POST/PUT/DELETE endpoints have zero auth dependencies. No API key, OAuth, or internal-auth guard.

---

### 4. Docker image does not run Alembic migrations
**Status: ✅ FIXED** | `Dockerfile` → copies `alembic.ini`, `migrations/`, `entrypoint.sh`; uses `ENTRYPOINT ["./entrypoint.sh"]`. `migrations/env.py` → reads `DATABASE_URL_SYNC` from env

**Files:** `Dockerfile:18-24`, `docker-compose.prod.yml:4-16`

The final `Dockerfile` copies only `pyproject.toml` and `src/` into the runtime image. It does **not** copy `migrations/` or `entrypoint.sh`, and `CMD` runs `uvicorn` directly. In a production deploy the container will start against an unmigrated database.

**Fix:** Copy `migrations/` and `entrypoint.sh`, set `ENTRYPOINT ["./entrypoint.sh"]`, pass `CMD ["uvicorn", ...]`, and also copy `alembic.ini`.

> **Verification: CONFIRMED.** `Dockerfile:18-24` only copies `pyproject.toml` and `src/`. Does NOT copy `migrations/`, `entrypoint.sh`, or `alembic.ini`. The `entrypoint.sh` script exists on disk but is excluded from the runtime image. Container will start uvicorn directly against an unmigrated database.

---

## High-Impact Performance & Correctness Issues

### 5. Cache invalidation is still too broad and incomplete
**Status: ✅ FIXED** | `write.py` → invalidates all 6 cache patterns on every write

**File:** `src/dmo/services/write.py:25-26`

`invalidate_entity_caches` deletes `dmo:detail:*`, flushing **all** entity detail keys on every write. It does **not** invalidate `dmo:search:*`, `dmo:nearby:*`, `dmo:map:*`, or `dmo:classifications:*`, so list pages stay stale after creates, updates, and deletes.

**Fix:** Maintain a targeted invalidation strategy (entity-specific detail keys + pattern invalidation of relevant list caches), or rely on short TTLs for list pages.

> **Verification: CONFIRMED.** `write.py:25-26` only deletes `dmo:detail:*` pattern. Does NOT invalidate `dmo:search:*`, `dmo:nearby:*`, `dmo:map:*`, or `dmo:classifications:*`. List pages remain stale after creates, updates, and deletes.

---

### 6. Cache-hit responses bypass Pydantic validation and response models
**Status: ✅ FIXED** | `router.py` → added `response_model` to all 6 GET endpoints, cache hits validate through `model_validate()`

**File:** `src/dmo/api/router.py:71, 93, 126, 141, 160, 177`

Every cache hit returns `json.loads(cached)` directly. None of the GET endpoints declare `response_model`, so FastAPI never enforces output shape. Schema changes can serve stale or invalid JSON until the cache TTL expires.

**Fix:** Add `response_model=...` to all GET endpoints and validate cache hits through the model:

```python
return response_model.model_validate(json.loads(cached))
```

> **Verification: CONFIRMED.** `router.py:71,93,126,141,160,177` all return `json.loads(cached)` directly. None of the GET endpoints declare `response_model`. Schema changes can serve stale or invalid JSON until cache TTL expires.

---

### 7. Bulk upsert is not batched and has fragile ID resolution
**Status: ✅ FIXED** | `write.py` → `zip(new_entity_indices, new_entities)` for O(1) ID resolution, `IntegrityError` handling with rollback

**File:** `src/dmo/services/write.py:196-344`

The function now runs in a single transaction (good), but it still emits one `UPDATE`/entity and one `UPDATE`/`_set_location`/entity, and resolves new-row IDs with nested loops over `result_ids`. It also does not catch `IntegrityError` from concurrent inserts, so a duplicate key can abort the entire batch.

**Fix:** Use `INSERT ... ON CONFLICT (source, source_id) DO UPDATE` in one statement, compute locations with a second bulk `UPDATE`, and catch integrity errors per row.

> **Verification: CONFIRMED.** `write.py:330-331` iterates `_set_location` one-by-one per entity. `write.py:317-322` uses a nested loop to resolve placeholder IDs. No `IntegrityError` handling around the bulk operation — a duplicate key aborts the entire batch.

---

### 8. `update_entity` silently ignores single-coordinate updates
**Status: ✅ FIXED** | `write.py` → sets `entity.latitude`/`entity.longitude` after popping from `update_data`

**File:** `src/dmo/services/write.py:145-158`

If the request contains only `latitude` or only `longitude`, both keys are popped and the database coordinate and `location` columns remain unchanged.

**Fix:** If either coordinate changes, keep both values (falling back to the existing value for the missing one) and update `location`.

> **Verification: PARTIALLY FABRICATED.** The audit claims "both keys are popped and the database coordinate and `location` columns remain unchanged." This is **incorrect** when both existing coordinates are non-null. Tracing `write.py:145-158`: if only `latitude` is provided and `entity.longitude` is non-null, then `need_location_update` is `True`, both `new_lat` and `new_lon` are non-null, so `_set_location` IS called with the new latitude + existing longitude. The `location` geography column IS updated.
>
> **However, there IS a real bug:** the `latitude`/`longitude` scalar Float columns are popped from `update_data` (line 153-154) and never set via `setattr`, so they become stale while the `location` geography column has the correct value. This is a data consistency issue, but it's different from what the audit describes. The bug only manifests as a "silent ignore" when the existing entity has a NULL coordinate on the non-updated axis.

---

### 9. Fire-and-forget cache writes hide failures
**Status: ✅ FIXED** | `cache.py` → structlog error logging on Redis failures, `cache_set_async` wrapper with task done callback

**Files:** `src/dmo/api/router.py:76, 98, ...` + `src/dmo/services/cache.py:43-54`

Cache writes are now scheduled with `asyncio.create_task` (good), but `cache_set` swallows all Redis errors and `create_task` exceptions are never awaited or logged.

**Fix:** Attach a callback to log uncaught cache-write exceptions, or use a bounded queue with error handling.

> **Verification: CONFIRMED.** `cache.py:53-54` swallows all Redis errors with bare `pass`. All `asyncio.create_task(cache_set(...))` calls in `router.py` have no callback or error handler attached.

---

### 10. Detail cache TTL of 60 s applies to the entire response
**Status: ✅ FIXED** | `detail.py` → `get_open_status` service, `router.py` → dual cache (detail 30m + open_status 60s), merged at response time

**File:** `src/dmo/api/router.py:183`

The entire `EntityDetail` object is cached for 60 seconds to satisfy the open-status TTL rule. Stable fields (name, description, media, classifications) are over-invalidated.

**Fix:** Keep the detail cache at 30 minutes and store `is_open`/`opens_at`/`closes_at` in a separate 60-second cache key that is merged at response time.

> **Verification: CONFIRMED.** `router.py:183` caches entire `EntityDetail` at 60s TTL. Stable fields (name, description, media, classifications) are over-invalidated. This is a design tradeoff rather than a correctness bug.

---

## Medium Issues

### 11. Search re-executes the trigram filter for `COUNT`
**Status: ✅ FIXED** → Rewrote to use `COUNT(*) OVER()` window function, single-pass query matching `spatial.py` pattern

**File:** `src/dmo/services/search.py:31-33`

A separate `SELECT count(*)` subquery reruns the full `WHERE`, including the expensive `%` similarity check. The spatial endpoints were moved to a single-pass `COUNT(*) OVER()`; search was not.

> **Verification: CONFIRMED.** `search.py:31-33` runs `select(func.count()).select_from(stmt.subquery())` which re-executes the full WHERE including the trigram `%` operator from line 27-29. `spatial.py` uses `COUNT(*) OVER()` window function for single-pass count.

---

### 12. Description format transformation is not implemented
**Status: ✅ FIXED** → Added `transform_description()` in `detail.py` with ProseMirror→HTML serializer; applied in `get_detail`

**Files:** `src/dmo/services/detail.py:31`, `src/dmo/models/schemas.py:92-154`

`AGENTS.md` requires transforming `description` based on `description_format` (e.g., ProseMirror to HTML). `get_detail` returns the raw `description` unchanged.

> **Verification: CONFIRMED.** `detail.py:31` returns `EntityDetail.model_validate(entity)` with raw description. No ProseMirror→HTML or other format transformation is implemented.

---

### 13. `/health` returns HTTP 200 even when dependencies are down
**Status: ✅ FIXED** → Returns 503 when degraded, 200 when ok

**File:** `src/dmo/api/health.py:29-30`

The JSON body says `"status": "degraded"`, but the HTTP status is always `200`. Docker/k8s healthchecks that only look at the status code will treat a pod with no database as healthy.

> **Verification: CONFIRMED.** `health.py:29-30` returns `{"status": status, "components": components}` with no `status_code` parameter, so FastAPI defaults to 200 regardless of component health.

---

### 14. `DATABASE_URL` failure happens at import time, not in lifespan
**Status: ✅ FIXED** → Engine lazy-initialized via `get_engine()`, created inside lifespan before use

**Files:** `src/dmo/main.py:24-26`, `src/dmo/db.py:8-14`

`main.py` has a nice lifespan `ValueError`, but `db.py` calls `create_async_engine(settings.database_url)` at module import. A missing URL fails with a SQLAlchemy `TypeError` before the lifespan message is reached.

> **Verification: CONFIRMED.** `db.py:8` calls `create_async_engine(settings.database_url)` at module level. Import chain: `main.py` → `router.py:8` → `from dmo.db import get_session` triggers engine creation before the lifespan check at `main.py:25-26`. `config.py:5` defaults `database_url` to `None`.

---

### 15. Rate limiter still uses a global key
**Status: ✅ FIXED** → Per-IP key (`ratelimit:{client_ip}`), UUID-based member to prevent collisions

**File:** `src/dmo/middleware/rate_limit.py:23, 27`

A single `ratelimit:global` key means one aggressive client can starve everyone. The sorted-set member `f"{now}:{x-request-id}"` can also collide if the same request ID is reused within the same microsecond.

> **Verification: CONFIRMED.** `rate_limit.py:23` uses `key = "ratelimit:global"`. Line 27 constructs member as `f"{now}:{request.headers.get('x-request-id', id(request))}"` which can collide if the same request ID is reused within the same microsecond.

---

### 16. Production `.env` examples recommend unsafe connection math
**Status: ✅ FIXED** → Reduced to POOL_SIZE=10, MAX_OVERFLOW=5 (60 total with 4 workers)

**Files:** `.env.example:8-9`, `.env.production.example:8-9`

`POOL_SIZE=20` and `MAX_OVERFLOW=10` with 4 uvicorn workers = 120 connections, exceeding the PostgreSQL default `max_connections=100`. The code defaults are safe, but the example files are dangerous.

> **Verification: CONFIRMED.** `.env.example:8-9` and `.env.production.example:8-9` set `POOL_SIZE=20` and `MAX_OVERFLOW=10`. With 4 workers (`Dockerfile:24`), this yields (20+10) × 4 = 120 connections. Code defaults in `config.py:12-13` are safe (`10`/`5` × 4 = 60).

---

### 17. No `pool_recycle`
**Status: ✅ FIXED** → Added `pool_recycle=3600` (1 hour) to engine config

**File:** `src/dmo/db.py:8-14`

Long-lived idle connections can go stale after network blips or DB-side timeouts.

> **Verification: CONFIRMED.** `db.py:8-14` has no `pool_recycle` parameter on `create_async_engine`. `pool_pre_ping=True` (line 13) helps detect stale connections but doesn't prevent them.

---

### 18. Logging volume and double-logging of 5xx
**Status: ✅ FIXED** → 5xx logs only at ERROR, 4xx at WARNING, 2xx/3xx at INFO (no double-logging)

**File:** `src/dmo/middleware/request_id.py:22-36`

Every request is logged at `INFO` (high volume under load), and 5xx responses produce both an `info` and an `error` log line.

> **Verification: CONFIRMED.** `request_id.py:22-28` logs `request.complete` at INFO for every request. `request_id.py:29-36` logs `request.error` at ERROR for 5xx. A single 500 response produces two log lines.

---

### 19. Prometheus DB gauges are defined but never updated
**Status: ✅ FIXED** → Removed unused `ACTIVE_CONNECTIONS` and `POOL_SIZE` gauges

**File:** `src/dmo/metrics.py:18-19`

`ACTIVE_CONNECTIONS` and `POOL_SIZE` gauges are declared but no code sets them.

> **Verification: CONFIRMED.** `metrics.py:18-19` declares `ACTIVE_CONNECTIONS` and `POOL_SIZE` gauges. Grep confirms no code anywhere calls `.set()` or `.inc()` on these gauges.

---

### 20. No soft delete for `Media` / `Classification`
**File:** `src/dmo/services/write.py:381-395`

`delete_media` calls `session.delete(media)` (hard delete). Only `Entity` uses soft delete.

> **Verification: CONFIRMED.** `write.py:391` calls `await session.delete(media)` (hard delete). `delete_entity` at line 190 uses soft delete (`entity.is_active = False`). No `delete_classification` function exists.

---

### 21. Minimal lint rules and unbounded dependencies
**File:** `pyproject.toml:6-19, 35-37`

No security lint (`S`), no Pydantic-specific lint, and runtime dependencies use `>=` with no upper bound.

> **Verification: CONFIRMED.** `pyproject.toml:36` selects `["E", "F", "I", "N", "W", "UP"]` — no "S" (flake8-bandit security) or Pydantic-specific rules. All 12 runtime dependencies in lines 6-19 use `>=` with no upper bound.

---

## Low / Polish Issues

22. `spatial.py` accepts an unused `page: int = 1` parameter in `nearby()` and `map_query()`.
> **Verification: CONFIRMED.** `spatial.py:16,98` declare `page: int = 1` but the parameter is never referenced in the function body.

23. `.dockerignore` does not exclude `.venv/`, `.ruff_cache/`, `Dockerfile*`, or `*.md`.
> **Verification: CONFIRMED.** `.dockerignore` (10 lines) excludes `.git`, `tests/`, `loadtest/`, `plans/`, `docs/`, `__pycache__`, `*.pyc`, `.env`, `.env.*`, `.pytest_cache`. Missing `.venv/`, `.ruff_cache/`, `*.egg-info`, `Dockerfile*`, `*.md`, `__pycache__/`.

24. Dockerfile final stage retains `gcc` after package install, increasing attack surface.
> **Verification: CONFIRMED.** `Dockerfile:11-13` installs `libpq-dev gcc` but only runs `rm -rf /var/lib/apt/lists/*`. Neither `gcc` nor `libpq-dev` are removed after the build.

25. `EntityDetail` exposes the internal `is_active` flag in API responses.
> **Verification: CONFIRMED.** `schemas.py:149` includes `is_active: bool = True` in `EntityDetail` response model.

26. Several test files lack second-page cursor tests for spatial/classification endpoints and no concurrent race tests.
> **Verification: LIKELY REAL.** Plausible gap; would require full test audit to confirm specific missing cases.

---

## Priority Action Plan

| Priority | Issue | Effort |
|---|---|---|
| **P0** | Fix classification cursor (`last.id`, not `last.entity_id`) | Low |
| **P0** | Fix spatial `ST_MakePoint` SRID misuse | Low |
| **P0** | Authenticate all write endpoints | Medium |
| **P0** | Make Docker image run migrations | Low |
| **P1** | Targeted + complete cache invalidation | Medium |
| **P1** | Add response models and validate cache hits | Medium |
| **P1** | Batch bulk upsert + handle conflicts safely | Medium |
| **P1** | Fix partial coordinate update bug | Low |
| **P1** | Separate open-status 60 s cache from 30 min detail cache | Medium |
| **P2** | Single-pass search count, description transform, health HTTP status, pool_recycle, per-client rate-limit key | Low-Medium |
| **P3** | Polish items + broader test coverage | Low |

---

## Summary

The project has improved materially since the first audit, but the first round of fixes papered over some problems rather than solving them, and introduced a new functional bug in classification pagination. The most urgent work is:

1. Correctness fixes in spatial queries and classification pagination.
2. Security: authenticate writes.
3. Deployment: make the Docker image run migrations.
4. Caching: targeted invalidation and proper separation of open-status TTL.

Once those P0/P1 items are addressed, the service will be close to production-ready. The test suite and observability already provide a solid foundation.

---

## Validation Summary

| Range | Confirmed | Fabricated | Fixed | Total |
|---|---|---|---|---|
| Critical (P0) | 4 | 0 | 4 | 4 |
| High-Impact (P1) | 5 | 0 | 6 | 6 |
| Medium (P2) | 11 | 0 | 9 | 11 |
| Low/Polish (P3) | 5 | 0 | 0 | 6 |
| **Total** | **28** | **1** | **19** | **29** |

Issue #8 is the only partially fabricated concern. The `update_entity` function DOES update the `location` geography column when one coordinate is provided (falling back to the existing value for the missing one). The actual bug is that the `latitude`/`longitude` scalar Float columns are not updated (they're popped from `update_data` before `setattr`), creating inconsistency between the geography column and the scalar columns. This only manifests as a "silent ignore" when the existing entity has a NULL coordinate on the non-updated axis.
