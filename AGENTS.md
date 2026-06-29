# AGENTS.md — dmo-on-premise

## Overview

Provider-agnostic data store and query API for tourism/POI data. Schema knows nothing about any data source.

**This is a read-only data store + query API.** External importers write data. This project does NOT fetch, scrape, or import data.

## Tech Stack

Python 3.12+ | FastAPI | PostgreSQL + PostGIS | asyncpg + SQLModel | Alembic | Redis | uv | structlog | Prometheus

## Critical Rules

1. **ZERO provider references** — no `google_*`, `dzt_*`, `rexby_*` prefixes anywhere
2. **`source` is VARCHAR, not enum** — new data source = zero schema changes
3. **`place_type` is VARCHAR, not enum** — new types emerge from data
4. **`attributes` JSONB is overflow** — Route/Tour, F&B, and provider-specific fields go here
5. **`source` + `source_id` unique** — same entity from different sources = separate rows
6. **JSONB numeric casts required** — use `(attributes->>'key')::numeric` for sorting/range queries
7. **Cache open-status with short TTL** — `is_open`, `opens_at`, `closes_at` change with time (60s TTL, separate from detail cache)
8. **All SQL must be parameterized** — use `text("... :param ...").bindparams(param=value)`, never f-strings
9. **N+1 queries forbidden** — use `COUNT(*) OVER()` window function for single-pass totals, batch IN() for related records
10. **Cache validated before serving** — all cache hits must pass through `model_validate()` or `TypeAdapter.validate_python()`
11. **String fields auto-stripped** — Pydantic `model_validator(mode='before')` handles whitespace, no `strip_whitespace` in Field()
12. **X-Request-ID must be UUID** — client-supplied IDs validated, non-UUID rejected and replaced
13. **Derived fields never set by importers** — `unified_category`, `unified_subcategory`, `unified_category_id`, `quality_score`, `enriched_at` are computed by admin scripts. `EntityCreate` silently ignores them.
14. **Slug immutability** — `unified_categories.slug` cannot be renamed after creation. Admin UI disables slug field on edit; backend excludes slug from UPDATE.

## Query Patterns

```sql
-- Spatial proximity (correct SRID — ST_SetSRID + ::geography)
ST_DWithin(location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)

-- Bounding box
ST_Intersects(location, ST_MakeEnvelope(:minLon, :minLat, :maxLon, :maxLat, 4326)::geography)

-- JSONB containment
attributes @> '{"key": "value"}'

-- JSONB numeric sort and filter
(attributes->>'distance_km')::numeric DESC

-- Text search via trigram (GIN gin_trgm_ops backed — use op('%'), never LIKE)
col(Entity.name).op('%')('search query')

-- Array overlap
region_names && ARRAY['Switzerland', 'Zurich']

-- Single-pass total count (all list endpoints)
COUNT(*) OVER() AS total

-- Advisory lock for serialized bulk operations
SELECT pg_advisory_xact_lock(:lock_id)
```

Always filter `is_active = TRUE` in search queries (entities, media, classifications).

## Response Format

- Paginated: `{ "results": [...], "total": N, "next_cursor": "base64...", "has_more": true }`
- Error: `{ "error": "NotFound", "message": "...", "code": 404, "request_id": "<uuid>" }`
- Include `attributes` in all entity responses
- Transform `description` based on `description_format` (prosemirror → HTML, etc.)
- Open-status (`is_open`, `opens_at`, `closes_at`) merged into detail response from separate 60s cache

## Caching

- **Redis** with SHA-256-hashed keys: `dmo:{endpoint}:{hash_of_params}`
- **TTLs:** 5 min (search/nearby/map/classifications/categories), 30 min (detail), 60 s (open-status)
- **Stampede protection:** `SET NX` distributed lock (`{key}:lock`) with 5s timeout. One request fetches, others poll cache every 50ms up to lock timeout.
- **Invalidation strategy:**
  - **Single-entity writes:** Clear all 8 patterns (`dmo:detail:*`, `dmo:open_status:*`, `dmo:search:*`, `dmo:nearby:*`, `dmo:map:*`, `dmo:classifications:*`, `dmo:categories:*`, `dmo:unified_categories:*`)
  - **Bulk upsert (>20 entities):** Single `dmo:*` wildcard SCAN (O(1) instead of O(n))
  - **Bulk upsert (≤20 entities):** Per-entity targeted invalidation
  - **Write order:** Invalidate cache BEFORE `session.commit()` — prevents stale reads
- **Error resilience:** Cache failures log to structlog via `_cache_task_done` callback. App degrades gracefully to DB.
- **Metrics:** `cache_hits_total`, `cache_misses_total` (Prometheus counters per endpoint)

## Security & Auth

| Rule | Detail |
|---|---|
| **API key required on writes** | All POST/PUT/DELETE endpoints use `X-API-Key` header via `fastapi.security.APIKeyHeader`. App fails startup if `API_KEY` env var is empty. |
| **OpenAPI security scheme** | `APIKeyHeader` type, `in: header`, `name: X-API-Key`. Write endpoints declare `tags=["Write"]` + `security=[{"APIKeyHeader": []}]`. |
| **SQL injection prevention** | All SQL parameterized via `bindparams()`. Statement timeout uses `set_config()` function call (not raw SET). |
| **XSS prevention** | `html.escape` + `bleach` on description transforms. ProseMirror serializer strips dangerous markup. |
| **Rate limiting** | Per-IP Redis sliding window via sorted sets. Uses `X-Forwarded-For` header in production (configurable via `TRUST_PROXY_HEADERS`). Count-before-add pattern prevents self-counting race. |
| **Request ID validation** | Client-supplied `X-Request-ID` validated as UUID. Non-UUID silently replaced with generated UUID. |
| **Input sanitization** | All string fields stripped via Pydantic `model_validator(mode='before')`. Coordinates enforce both-or-neither. |

## Conventions

### Code Style
- Type hints on all signatures, async throughout
- Service layer: routers → services → database
- SQLModel for tables, Pydantic for response schemas (V3-ready, no `strip_whitespace` in Field)
- Alembic for all schema changes, `op.execute()` for PostGIS ops
- Cursor pagination: use `services/pagination.py` encode/decode helpers (returns strict `UUID | int`, never string)
- Request ID: stored in `request.state.request_id`, read from there in handlers
- `ruff check` and `ruff format` must pass on `src/` and `tests/` before commit

### Route Ordering (critical — prevents path conflicts)
```
GET  /classifications/categories   ← BEFORE /classifications
GET  /classifications              ← BEFORE generic path
DELETE /classifications/{id}       ← BEFORE generic path
DELETE /media/{media_id}           ← BEFORE generic path
GET  /unified-categories           ← BEFORE catch-all (single-segment, no collision)
GET  /{source}/{source_id}         ← LAST (catch-all path param)
```

### OpenAPI Tags
| Tag | Endpoints |
|-----|-----------|
| `Read` | All 7 GET endpoints (search, nearby, map, detail, classifications, categories, unified-categories) |
| `Write` | All 8 POST/PUT/DELETE endpoints |
| `System` | `/health`, `/metrics` |

### API Key Pattern
```python
from fastapi.security import APIKeyHeader
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(x_api_key: str = Depends(api_key_header)) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

### Pydantic V3-Ready String Stripping
```python
# DO NOT use Field(strip_whitespace=True) — deprecated in V2, removed in V3
# Use model_validator(mode='before') instead:
@model_validator(mode='before')
@classmethod
def strip_strings(cls, data: Any) -> Any:
    if isinstance(data, dict):
        return {k: v.strip() if isinstance(v, str) else v for k, v in data.items()}
    return data
```

## Architecture

```
src/dmo/
├── main.py              # FastAPI app, lifespan, middleware, exception handlers
├── config.py            # Settings (env vars, pydantic BaseSettings)
├── db.py                # Connection pool (lazy-init), session factory with timeout
├── exceptions.py        # AppError class, global exception handlers (504/404/400/etc)
├── logging.py           # structlog setup (JSON renderer)
├── metrics.py           # Prometheus counters/histograms
├── api/
│   ├── router.py        # All REST endpoints (read + write) — tags + auth
│   ├── health.py        # /health with DB + Redis checks (1.5s timeout each)
│   └── metrics.py       # /metrics Prometheus endpoint
├── admin/
│   ├── router.py        # Admin UI routes (taxonomy, mappings, scripts)
│   └── templates/       # Jinja2 + HTMX admin templates
├── admin_scripts/       # Auto-discovered via pkgutil
│   ├── base.py          # AdminScript ABC, ScriptMeta, ScriptParameter, ScriptResult
│   ├── registry.py      # Auto-discovery and script listing
│   ├── normalize_place_types.py   # Fix: strip/lowercase place_type
│   ├── unify_place_types.py       # Unify: map entities to unified categories
│   ├── extract_attributes.py      # Fix: extract website/thumbnail
│   ├── unify_classifications.py   # Unify: backfill classifications
│   ├── clean_dzt_data.py          # Heal: fix DZT country/region
│   └── ...              # Additional scripts (enrich, score, heal)
├── middleware/
│   ├── request_id.py    # X-Request-ID generation, UUID validation, request logging
│   └── rate_limit.py    # Per-IP Redis sliding-window (X-Forwarded-For aware, count-before-add)
├── models/
│   ├── database.py      # SQLModel tables (Entity, Media, Classification, Route, UnifiedCategory, PlaceTypeMapping)
│   └── schemas.py       # Pydantic request/response schemas (V3-ready, no strip_whitespace)
└── services/
    ├── cache.py         # SHA-256-hashed Redis cache + stampede protection (5s lock)
    ├── classifications.py  # Classifications list (single-pass) + categories
    ├── detail.py        # Detail query with asyncio.gather + open-status merge + description transform
    ├── pagination.py    # Cursor encode/decode (strict UUID/int validation)
    ├── search.py        # Text search (pg_trgm op('%')) + single-pass COUNT(*) OVER()
    ├── spatial.py       # Nearby (ST_DWithin) + map (ST_Intersects) with cursor pagination
    ├── taxonomy.py      # Unified taxonomy tree + category level detection
    └── write.py         # Create/update/delete + advisory locks + cache invalidation
```

## API Endpoints

### Read (public)
```
GET  /search?q=&source=&place_type=&country=&unified_category=&page_size=&cursor=
GET  /nearby?lat=&lon=&radius_km=&source=&place_type=&unified_category=&page_size=&cursor=
GET  /map?bbox=&source=&place_type=&unified_category=&page_size=&cursor=
GET  /{source}/{source_id}
GET  /classifications/categories
GET  /classifications?entity_id=&category=&value_code=&page_size=&cursor=
GET  /unified-categories
```

### Write (requires X-API-Key header)
```
POST /entities              — create entity (201)
POST /entities/bulk         — bulk upsert, max 1000 (201)
PUT  /{source}/{source_id}  — update entity (200)
DELETE /{source}/{source_id} — soft-delete entity (200)
POST /media                 — add media (201)
DELETE /media/{media_id}    — soft-delete media (200)
POST /classifications       — add classification (201)
DELETE /classifications/{classification_id} — soft-delete classification (200)
```

### System
```
GET  /health   — DB + Redis health (200 or 503, 1.5s per-component)
GET  /metrics  — Prometheus metrics
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | *(required)* | Async DB connection (asyncpg) |
| `DATABASE_URL_SYNC` | *(required)* | Sync DB connection for Alembic |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `API_KEY` | *(required)* | API key for write endpoints (fails startup if empty) |
| `CACHE_TTL` | `300` | Default cache TTL in seconds |
| `RATE_LIMIT_ENABLED` | `true` | Enable/disable rate limiting |
| `RATE_LIMIT_MAX_REQUESTS` | `1000` | Max requests per window per IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window in seconds |
| `TRUST_PROXY_HEADERS` | `true` | Use `X-Forwarded-For` for client IP |
| `POOL_SIZE` | `10` | DB connection pool size per worker |
| `MAX_OVERFLOW` | `5` | DB max overflow per worker |
| `QUERY_TIMEOUT_SECONDS` | `10.0` | Read query timeout (write uses `REQUEST_TIMEOUT_SECONDS`) |
| `REQUEST_TIMEOUT_SECONDS` | `30.0` | HTTP request timeout |
| `SLOW_REQUEST_THRESHOLD_MS` | `500.0` | Log warning for requests exceeding this |
| `LOG_LEVEL` | `INFO` | Logging level |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins |

## Full Schema

See `plans/unified-dmo-schema.md` for complete schema, field mappings, design decisions, and implementation warnings.

## Data Integrity

| Feature | Detail |
|---|---|
| **Advisory locks** | `pg_advisory_xact_lock` scoped by `hash(source) % (2**31)` — same-source serialized, different-source concurrent |
| **IntegrityError retry** | On concurrent conflict, rolls back, re-acquires lock, re-checks existing entities, retries |
| **REPEATABLE_READ** | Session isolation level set at pool level |
| **Pre-commit invalidation** | Cache invalidated BEFORE commit, preventing other transactions from reading stale data |
| **Soft deletes** | `DELETE` sets `is_active=false`. Search/list queries always filter `is_active=true` |
| **Coordinate consistency** | Entity create/update validates both-or-neither for latitude/longitude |

## Testing

- Tests use real PostGIS database (not SQLite) due to PostGIS type requirements
- Cache disabled in tests via autouse fixture
- Session cleanup runs at start AND end of each test
- Package-locally-scoped imports within test functions (avoid top-level imports that trigger app init)
- **Test VM**: `root@10.0.1.8` — use for all test runs (isolated from Staging VM)
- **Test DB URL**: `postgresql+asyncpg://postgres:changeme@10.0.1.8:5432/dmo`
- **Test Redis URL**: `redis://10.0.1.8:6379`
- Run: `TEST_DB_URL=postgresql+asyncpg://postgres:changeme@10.0.1.8:5432/dmo TEST_REDIS_URL=redis://10.0.1.8:6379 uv run pytest tests/`
- Lint: `uv run ruff check src/ tests/`
- Format: `uv run ruff format src/ tests/`
- **Must pass before commit:** all tests + ruff check (244 tests, 0 lint errors)

### Test Structure (22 test files)

| File | What It Covers |
|------|---------------|
| `test_cache_stampede.py` | Stampede lock, waiter retry, cache hit/miss, TTL |
| `test_classifications.py` | List classifications, categories, filters |
| `test_concurrency.py` | Concurrent bulk upsert, advisory lock serialization |
| `test_detail.py` | Entity detail, open-status merge, media/classification includes |
| `test_errors.py` | 404, 400, 401, 504 responses, request ID propagation, UUID validation |
| `test_health.py` | Health check endpoint, DB/Redis status |
| `test_main.py` | App startup, lifespan, middleware chain |
| `test_open_status.py` | Open-status caching (separate 60s TTL) |
| `test_pagination.py` | Cursor encode/decode, type validation, edge cases |
| `test_rate_limit.py` | Rate limit enable/disable, exceeded, proxy headers, IP isolation |
| `test_search.py` | Text search, filters, pagination |
| `test_spatial.py` | Nearby, map, bounding box, cursor pagination |
| `test_timeout.py` | Statement timeout, request timeout, 504 response |
| `test_write.py` | Create, update, delete entities/media/classifications, bulk upsert, cache invalidation |
| `test_xss.py` | XSS prevention in description transforms |

## OpenAPI Docs

- Regenerate after any API or schema changes (routers, schemas, endpoints, security)
- Run: `uv run python scripts/export-openapi.py && npx redocly build-docs docs/openapi.json -o docs/index.html --config docs/redocly.yaml`
- Output: `docs/openapi.json` (spec) + `docs/index.html` (standalone Redoc UI, ~448 KiB)
- Uses `APIKeyHeader` security scheme, `Read`/`Write`/`System` tags

## Key Implementation Notes

- **Spatial queries**: `text("... :param ...").bindparams(param=value)` pattern for parameterized PostGIS. Use `ST_SetSRID(ST_MakePoint(...), 4326)::geography`, NOT raw `ST_MakePoint`.
- **Write API spatial**: ORM insert first, then raw SQL `UPDATE` to set `location` GEOGRAPHY column (PostGIS type requires separate step).
- **Route ordering**: `/classifications/categories`, `/classifications`, `/media/{media_id}`, `/classifications/{classification_id}` before `/{source}/{source_id}`.
- **Request ID**: stored on `request.state.request_id`, NOT in headers. Generated UUID validated on input.
- **Cursor pagination**: base64-encoded JSON `{"id": "<uuid_or_int>", "sort": <sort_key>}`. Strict types — invalid cursors raise `AppError("InvalidCursor", 400)`.
- **Detail optimization**: `asyncio.gather` for concurrent media/classification/entity queries + separate open-status fetch with 60s TTL.
- **Request timeout**: `asyncio.wait_for` middleware, 30s default, returns structured 504 JSON.
- **Error responses**: `{error, message, code, request_id}` format via global exception handlers.
- **Statement timeout**: Set via `SELECT set_config('statement_timeout', :timeout, false)` (parameterized function call, not raw SET). Read sessions: 10s. Write sessions: 30s.
- **Pydantic V3 compatibility**: No `strip_whitespace` in `Field()`. All string stripping via `model_validator(mode='before')`.
- **Unified category filtering**: `unified_category` query param auto-detects top-level vs leaf via `get_category_level()`. Top-level slugs filter `unified_category` column; leaf slugs filter `unified_subcategory` column.
- **Slug immutability**: `unified_categories.slug` cannot be renamed after creation. Admin UI disables the slug field on edit; backend PUT endpoint excludes slug from the UPDATE statement entirely.

## Common Pitfalls

1. **Don't add new endpoints after `/{source}/{source_id}`** — it's a catch-all that would swallow your route.
2. **Don't use f-strings in SQL** — even for utility commands. Use `set_config()` for timeouts, `bindparams()` for everything else.
3. **Don't forget `is_active=true`** — all read queries need it; it's easy to miss in new services.
4. **Don't use `LIKE`** — use `op('%')` which leverages the GIN trigram index.
5. **Don't add `strip_whitespace=True`** to new fields — use the model-level `strip_strings` validator or add to existing one.
6. **Don't import test fixtures at module level** — use function-local imports to avoid triggering app initialization.
7. **Don't cache open-status for more than 60s** — `is_open`/`opens_at`/`closes_at` are time-sensitive.
8. **Don't hardcode lock IDs** — use `hash(source) % (2**31)` for per-source advisory locks.
9. **Don't trust client IP directly** — use `X-Forwarded-For` when `TRUST_PROXY_HEADERS=true`.
10. **Don't set derived fields in importers** — `unified_category`, `unified_subcategory`, `unified_category_id`, `quality_score`, `enriched_at` are computed by admin scripts. Importers set `place_type` only.

## Production Deployment

```bash
# Production stack with health checks, resource limits, and env vars
docker compose -f docker-compose.prod.yml up -d
```

- Dockerfile uses multi-stage build with `uv` for lockfile-based dependency installation
- Entrypoint runs Alembic migrations before starting Uvicorn with 4 workers
- Health check uses `/health` endpoint (1.5s per component, 3s total < Docker's 5s default)
- API key required at startup — app refuses to start if `API_KEY` is empty

## Audit History

This project has been through 6 audit cycles. All issues are fixed.

| Audit | Date | Issues | Grade | Status |
|-------|------|--------|-------|--------|
| v1 (`plans/AUDIT.md`) | Jun 13 | 40 issues | C+ | ✅ Fixed |
| v2 (`plans/AUDIT-REAUDIT.md`) | Jun 13 | 29 issues | C | ✅ Fixed |
| v3 (`plans/AUDIT-FINAL.md`) | Jun 14 | 26 verifications | B+ | ✅ Verified |
| v4 (`plans/AUDIT-V5-FINAL.md`) | Jun 14 | 3 P1 bugs | A- | ✅ Fixed |
| v5 (`plans/AUDIT-V6-FINAL.md`) | Jun 14 | 17 issues | A | ✅ Fixed |

For detailed findings, fix notes, and methodology, see `plans/AUDIT-V6-FINAL.md`.

## Files Never to Edit Directly

| File | Why | How to Update |
|------|-----|---------------|
| `docs/openapi.json` | Generated from FastAPI app | `uv run python scripts/export-openapi.py` |
| `docs/index.html` | Generated from openapi.json | `npx redocly build-docs docs/openapi.json -o docs/index.html --config docs/redocly.yaml` |
| `uv.lock` | Lockfile managed by uv | `uv lock` or `uv sync` |
| Migration files | Alembic auto-generates these | `uv run alembic revision --autogenerate -m "description"` |
