# Unified My3Pai Place Service

A provider-agnostic data store and query API for tourism and POI data. Ingests places from any source into a single normalized schema, then serves them through a fast, cached, spatially-aware REST API.

**Status:** Production-ready | **Tests:** 231 passing | **Lint:** ruff clean | **Grade:** A

---

## Problem

Tourism data arrives from many sources — Google Places, national DMOs, local providers, open datasets — each with its own schema, field naming, and data model. Building a unified query layer on top of heterogeneous sources typically means either:

- **Schema coupling:** Adding provider-specific columns (`google_*`, `dzt_*`, `rexby_*`) that make the database brittle and impossible to extend without migrations.
- **N+1 queries:** Hitting each provider individually at query time, creating latency and fragility.
- **Lost flexibility:** Enum-based types and rigid schemas that break when a new category or data source appears.

## Solution

A single normalized schema that knows nothing about data providers. External importers map their data into this schema, and the API serves everything through one interface. New sources or place types require **zero schema changes**.

### Design Principles

| Principle | How It Works |
|---|---|
| **Provider-agnostic schema** | No provider-specific columns. `source` is a `VARCHAR`, not an enum. |
| **Evolving types** | `place_type` is a `VARCHAR` — new categories emerge from data, not migrations. |
| **JSONB overflow** | Domain-specific fields (routes, F&B, provider extras) live in a GIN-indexed `attributes` column. |
| **Source uniqueness** | `(source, source_id)` unique constraint — same entity from different sources = separate rows. |
| **Format-agnostic content** | `description` stores raw format; `description_format` tells the API how to transform it. |
| **Soft deletes** | Entities, media, and classifications are deactivated (`is_active = false`) rather than destroyed. |
| **Zero-trust input** | String fields stripped, X-Request-ID validated as UUID, API key on all writes. |

## Tech Stack

- **Python 3.12+** — FastAPI, fully async
- **PostgreSQL + PostGIS** — Spatial indexing, trigram search, JSONB queries
- **Redis** — Response caching, rate limiting, cache stampede protection
- **SQLModel + asyncpg** — Type-safe ORM with raw SQL where it counts
- **Alembic** — Database migrations
- **structlog** — Structured JSON logging with request ID tracing
- **Prometheus** — Request metrics and cache hit/miss tracking
- **uv** — Lockfile-based package management

## Architecture

```
src/dmo/
├── main.py              # FastAPI app, lifespan, middleware, exception handlers
├── config.py            # Settings via pydantic-settings (env vars)
├── db.py                # Async engine (lazy-init), pool config, session factory
├── exceptions.py        # AppError class, global exception handlers
├── logging.py           # structlog JSON renderer
├── metrics.py           # Prometheus counters/histograms
├── api/
│   ├── router.py        # All REST endpoints (read + write)
│   ├── health.py        # /health with DB + Redis checks (1.5s timeout each)
│   └── metrics.py       # /metrics Prometheus endpoint
├── middleware/
│   ├── request_id.py    # X-Request-ID generation, UUID validation, logging
│   └── rate_limit.py    # Per-IP Redis sliding-window rate limiter (X-Forwarded-For aware)
├── models/
│   ├── database.py      # SQLModel tables (Entity, Media, Classification, Route)
│   └── schemas.py       # Pydantic request/response schemas (V3-ready)
└── services/
    ├── cache.py         # SHA-256-hashed Redis cache + stampede protection
    ├── classifications.py  # Classifications list (single-pass) + categories
    ├── detail.py        # Detail query with asyncio.gather + description transform
    ├── pagination.py    # Cursor encode/decode helpers (strict type validation)
    ├── search.py        # Text search (pg_trgm) with single-pass COUNT(*) OVER()
    ├── spatial.py       # Nearby (ST_DWithin) + map (ST_Intersects) with cursor pagination
    └── write.py         # Create/update/delete + advisory locks + cache invalidation
```

**Layer pattern:** Routers → Services → Database

## Data Model

### `entities` — Core table

All tourism items live in a single table, differentiated by `place_type`.

| Field Group | Fields |
|---|---|
| **Identity** | `id` (UUID), `source`, `source_id`, `source_url` |
| **Info** | `name`, `slug`, `summary`, `description`, `description_format` |
| **Classification** | `place_type`, `category_class`, `secondary_types` (array) |
| **Collection** | `collection_id`, `collection_name`, `collection_slug` |
| **Location** | `location` (GEOGRAPHY Point), `latitude`, `longitude`, `country`, `region`, `locality`, `region_names` (array), `address`, `postal_code` |
| **Media** | `thumbnail_url`, `icon_url`, `website`, `map_screenshot_url` |
| **Hours** | `is_open`, `opens_at`, `closes_at`, `opening_hours`, `recommended_season`, `business_status` |
| **Contact** | `phone`, `email`, `booking_link`, `menu_url`, `order_url`, `reservations_url` |
| **Pricing** | `currency`, `price_min`, `price_max`, `price_level` |
| **Accessibility** | `is_barrier_free`, `wheelchair_accessible` |
| **Popularity** | `is_featured`, `favorite_count`, `rating`, `reviews_count` |
| **Overflow** | `attributes` (JSONB) |
| **Lifecycle** | `is_active`, `imported_at`, `updated_at` |

**Indexes:** GIST on `location`, GIN on `attributes`, GIN trigram on `name` and `summary`, B-tree on `(lat, lon)`, partial on `rating`, partial on `slug`, composite on `(source, is_active)`, `(place_type, is_active)`, `(country, is_active)`, `(collection_id, is_active)`.

### `media` — Images, videos, audio per entity

`id`, `entity_id` (FK), `media_type`, `url`, `name`, `keywords`, `copyright_holder`, `publisher`, `width`, `height`, `encoding_format`, `sort_order`, `attributions` (JSONB), `poster_url`, `is_muted`, `is_active`

### `classifications` — Categorical tags per entity

`id`, `entity_id` (FK), `category`, `value_code`, `value_title`, `is_active`

### `routes` — Geometries for trails and tours

`id`, `entity_id` (FK), `geometry` (GEOMETRY), `elevation_profile` (JSONB), `fetched_at`

## API

### Read Endpoints

| Method | Path | Description | Cache TTL |
|---|---|---|---|
| `GET` | `/search` | Full-text search with filters (q, source, place_type, country) | 5 min |
| `GET` | `/nearby` | Proximity search (lat/lon/radius_km) | 5 min |
| `GET` | `/map` | Bounding-box spatial query (minLon,minLat,maxLon,maxLat) | 5 min |
| `GET` | `/{source}/{source_id}` | Entity detail + media + classifications | 30 min |
| `GET` | `/classifications` | List classifications with filters | 5 min |
| `GET` | `/classifications/categories` | Distinct category values | 5 min |
| `GET` | `/health` | Health check (DB + Redis, returns 503 if degraded) | — |
| `GET` | `/metrics` | Prometheus metrics | — |

**Open-status caching:** The detail endpoint uses split-cache TTLs — stable fields (name, description, media) are cached for 30 min, while time-sensitive fields (`is_open`, `opens_at`, `closes_at`) are cached separately for 60 seconds and merged at response time.

### Write Endpoints (API key required)

| Method | Path | Description | Status |
|---|---|---|---|
| `POST` | `/entities` | Create entity | 201 |
| `POST` | `/entities/bulk` | Bulk upsert (max 1000, advisory-locked) | 201 |
| `PUT` | `/{source}/{source_id}` | Update entity | 200 |
| `DELETE` | `/{source}/{source_id}` | Soft-delete entity | 200 |
| `POST` | `/media` | Add media to entity | 201 |
| `DELETE` | `/media/{media_id}` | Soft-delete media | 200 |
| `POST` | `/classifications` | Add classification | 201 |
| `DELETE` | `/classifications/{classification_id}` | Soft-delete classification | 200 |

### Pagination

All list endpoints use **cursor-based pagination** (keyset pagination) for stable, O(1) page access at any depth:

```json
{
  "results": [...],
  "total": 1250,
  "next_cursor": "eyJpZCI6ICIzZmE...IiwgInNvcnQiOiAyMDI1LTAxLTAxIn0=",
  "has_more": true
}
```

Cursors are base64-encoded JSON with strict UUID/int validation. Malformed cursors return a `400 InvalidCursor` error.

### Error Responses

```json
{
  "error": "NotFound",
  "message": "Entity not found: some_source/abc123",
  "code": 404,
  "request_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

All errors follow this format. 5xx errors are logged at ERROR level; 4xx at WARNING.

## Caching

- **Redis** with SHA-256-hashed keys: `dmo:{endpoint}:{hash_of_params}`
- **TTLs:** 5 min (search/nearby/map/classifications/categories), 30 min (detail), 60 s (open-status)
- **Stampede protection:** `SET NX` distributed lock — only one request fetches on cache miss. Waiters poll with 50ms intervals for up to 5 seconds before falling through.
- **Error resilience:** Cache failures log to structlog and degrade gracefully. The app continues serving from the database.
- **Invalidation:** Single-entity writes clear all 7 cache patterns. Bulk upserts with >20 entities use a single `dmo:*` wildcard purge. Cache invalidation runs before commit for consistency.
- **Instrumentation:** `cache_hits_total` and `cache_misses_total` Prometheus counters per endpoint.

## Security & Hardening

| Feature | Detail |
|---|---|
| **API key authentication** | All write endpoints require `X-API-Key` header. App fails to start if `API_KEY` is not set. |
| **SQL injection prevention** | All SQL uses parameterized queries — `bindparams()` for raw SQL, ORM for everything else. Session timeouts set via `set_config()` (not f-string). |
| **XSS prevention** | Description transforms use `html.escape` + `bleach` with protocol blocking. ProseMirror → HTML serializer strips dangerous markup. |
| **Rate limiting** | Per-IP sliding-window via Redis sorted sets. Supports `X-Forwarded-For` header for production deployments behind reverse proxies (configurable). |
| **Request ID validation** | Client-supplied `X-Request-ID` headers are validated as UUIDs. Non-UUID values are silently replaced with a generated UUID. |
| **Input sanitization** | All string fields are stripped of whitespace via Pydantic `model_validator(mode='before')`. Coordinate validation enforces both-or-neither for `lat`/`lon`. |
| **Health checks** | Returns HTTP 503 when components are degraded (not 200 with degraded body). 1.5s per-component timeout, total < Docker's 5s HEALTHCHECK limit. |

## Concurrency & Data Integrity

| Feature | Detail |
|---|---|
| **Advisory locks** | Bulk upserts acquire `pg_advisory_xact_lock` scoped by source hash — different source imports run concurrently, same-source imports serialize. |
| **IntegrityError retry** | On concurrent conflict, rolls back, re-acquires lock, re-checks existing entities, retries. |
| **REPEATABLE_READ isolation** | Consistent reads throughout transactions, preventing phantom reads. |
| **Pre-commit invalidation** | Cache is invalidated before transaction commit, preventing stale reads. |
| **Soft deletes** | `DELETE` sets `is_active=false` on entities, media, and classifications. Read queries filter `is_active=true`. |
| **Coordinate consistency** | Entity create/update validates both-or-neither for `latitude`/`longitude`. Updates fall back to existing values for partial updates. |

## Performance

| Feature | Detail |
|---|---|
| **Async I/O** | Non-blocking DB queries, Redis calls, and HTTP responses throughout. |
| **Connection pooling** | Configurable pool (default 10 + 5 overflow × 4 workers = 60 total, safe under PG max_connections=100). |
| **Cursor pagination** | No OFFSET/N degradation — O(1) page access at any depth. Spatial and classification endpoints use valid, tested cursor pagination. |
| **GIST spatial index** | Fast Haversine (`ST_DWithin`) and bounding-box (`ST_Intersects`) queries with correct SRID handling (`ST_SetSRID` + `::geography`). |
| **pg_trgm GIN index** | Fuzzy text search on `name` and `summary` via `%` operator, avoiding sequential scans. |
| **JSONB GIN index** | Queryable overflow attributes without schema changes. |
| **Single-pass queries** | All list endpoints use `COUNT(*) OVER()` window function — no separate COUNT query. One round-trip per page. |
| **Parallel detail queries** | `asyncio.gather` fetches entity, media, and classifications concurrently. |
| **Strict query timeouts** | Read sessions: 10s. Write sessions: 30s. Enforced at DB level via `statement_timeout`. Requests timeout at 30s via `asyncio.wait_for`. |
| **Bulk batch optimization** | Bulk upserts with >20 entities use single-pass cache invalidation (`dmo:*`) instead of per-entity cascade. |
| **Slow request logging** | Requests exceeding 500ms are flagged in structured logs. |

## Benchmark Results

Live benchmarks against staging (8,177 Swiss tourism entities, PostGIS 16.4, Redis 7).

### Cache Performance

| Endpoint | Query | MISS (DB) | HIT (Redis) | Speedup |
|---|---|---|---|---|
| `/search` | text + filters | 24ms | 26ms | — |
| `/nearby` | small radius (2km) | 21ms | 18ms | 1.2x |
| `/nearby` | large radius (50km) | 367ms | 297ms | 1.2x |
| `/map` | tight bbox | 249ms | 237ms | 1.0x |
| `/map` | wide bbox (all CH) | 496ms | 492ms | 1.0x |
| `/classifications` | filtered | 26ms | 19ms | 1.4x |
| `/{source}/{source_id}` | detail + media | 23ms | 23ms | — |

> **Note:** Individual request timing shows minimal delta because network RTT to the VM dominates. The real benefit is **DB load reduction** under concurrent load.

### Concurrent Load — Stampede Protection

```
10 simultaneous requests to /nearby (50km radius, 50 results)
├─ 1 request: MISS → fetched from PostgreSQL
└─ 9 requests: HIT  → served from Redis cache

Result: 90% cache hit rate under concurrent load
DB load reduction: 10x (1 query instead of 10)
```

All endpoints confirmed: `/search`, `/nearby`, `/map`, `/classifications`, `/{source}/{source_id}`.

### Cache Architecture

- **SHA-256 hashed keys** — `dmo:{endpoint}:{hash(params)}` — consistent, collision-free
- **Distributed lock** — `SET NX` prevents stampede on cache miss (5s timeout, 50ms poll)
- **Split TTLs** — 5min (search/list), 30min (detail), 60s (open-status)
- **Pre-commit invalidation** — writes clear cache before DB commit — zero stale reads
- **Graceful degradation** — Redis failures log to structlog, app falls back to DB

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 15+ with PostGIS extension
- Redis 7+
- uv (package manager)

### Local Development

```bash
# Start dependencies
docker compose up -d

# Install dependencies
uv sync

# Run migrations
uv run alembic upgrade head

# Start the server
uv run uvicorn dmo.main:app --reload
```

The API is available at `http://localhost:8000` with auto-generated docs at `/docs` (Swagger UI) and `/redoc` (ReDoc).

### Configuration

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
| `TRUST_PROXY_HEADERS` | `true` | Use `X-Forwarded-For` for client IP (disable for direct connections) |
| `POOL_SIZE` | `10` | DB connection pool size per worker |
| `MAX_OVERFLOW` | `5` | DB max overflow per worker |
| `QUERY_TIMEOUT_SECONDS` | `10.0` | Read query timeout (write uses `REQUEST_TIMEOUT_SECONDS`) |
| `REQUEST_TIMEOUT_SECONDS` | `30.0` | HTTP request timeout |
| `SLOW_REQUEST_THRESHOLD_MS` | `500.0` | Log warning for requests exceeding this |
| `LOG_LEVEL` | `INFO` | Logging level |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins |

## Testing

Tests run against a real PostgreSQL + PostGIS database (not SQLite) to ensure spatial query correctness. Cache is disabled in tests via autouse fixture.

```bash
# Run all tests
uv run pytest tests/

# Run with verbose output
uv run pytest tests/ -v

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/
```

**Test coverage:** 231 tests across 22 test files covering search, nearby, map, detail, classifications, CRUD, bulk upserts, cache stampede, concurrency, rate limiting, XSS, security, error resilience, coordinate validation, timeouts, health checks, auth, and open-status caching.

## Production Deployment

```bash
# Production stack with health checks, resource limits, and env vars
docker compose -f docker-compose.prod.yml up -d
```

The Dockerfile uses a multi-stage build with `uv` for fast, lockfile-based dependency installation. The entrypoint runs **Alembic migrations** before starting Uvicorn with 4 workers. Health checks use the `/health` endpoint with a 5-second timeout.

**Before deploying to heavy traffic:** Run load tests (spike + soak + write-path) using the k6 script in `loadtest/`.

## Backup & Restore

Production-ready scripts for PostgreSQL database backup and restore, supporting both local and remote (SSH) environments.

### Backup

```bash
# Local backup
./scripts/db-backup.sh local

# Remote backup (staging VM)
./scripts/db-backup.sh remote staging

# Custom retention (keep last 14 backups)
./scripts/db-backup.sh local --retention 14
```

**Features:**
- `pg_dump -Fc` (custom format) — compressed, parallel-restore-capable
- SHA-256 checksums + `metadata.json` (table count, dump size, PostGIS version)
- Auto-cleanup — keeps last N backups (default: 7)
- Remote mode — runs on VM via SSH, copies dump to local machine
- Timestamped directories — `backups/YYYY-MM-DD_HHMMSS[_remote_host]/`

### Restore

```bash
# Local restore from latest backup
./scripts/db-restore.sh local

# Remote restore (staging VM)
./scripts/db-restore.sh remote staging

# Restore specific backup
./scripts/db-restore.sh local backups/2026-06-15_120000/dump.dump

# Force without confirmation
./scripts/db-restore.sh local --force backups/2026-06-15_120000/dump.dump
```

**Features:**
- Integrity check — validates SHA-256 checksum before restore
- Stops API service during restore, restarts after
- Runs Alembic migrations post-restore for schema sync
- Interactive confirmation (use `--force` to skip)
- Drops and recreates database for clean restore

### Backup Directory Structure

```
backups/
├── 2026-06-15_120000/
│   ├── dump.dump          # pg_dump -Fc file
│   ├── dump.sha256        # checksum
│   └── metadata.json      # table count, size, PostGIS version
└── 2026-06-15_120000_10.0.0.93/
    ├── dump.dump
    ├── dump.sha256
    └── metadata.json
```

## Query Patterns

```sql
-- Spatial proximity (correct SRID handling)
ST_DWithin(location, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :radius_m)

-- Bounding box
ST_Intersects(location, ST_MakeEnvelope(:minLon, :minLat, :maxLon, :maxLat, 4326)::geography)

-- JSONB containment
attributes @> '{"key": "value"}'

-- JSONB numeric sort and filter
(attributes->>'distance_km')::numeric DESC

-- Text search via trigram similarity (GIN gin_trgm_ops backed)
col(Entity.name).op('%')('search query')

-- Array overlap
region_names && ARRAY['Switzerland', 'Zurich']

-- Single-pass total count
COUNT(*) OVER() AS total

-- Advisory lock for bulk operations
SELECT pg_advisory_xact_lock(:lock_id)
```

## Audit History

This project has been through 6 audit cycles. All issues identified across all cycles are fixed and verified.

| Audit | Date | Issues | Grade | Status |
|-------|------|--------|-------|--------|
| v1 (`AUDIT.md`) | Jun 13 | 40 issues | C+ | ✅ All fixed |
| v2 (`AUDIT-REAUDIT.md`) | Jun 13 | 29 issues | C | ✅ 26/26 fixes confirmed |
| v3 (`AUDIT-FINAL.md`) | Jun 14 | 26 verifications | B+ | ✅ All verified |
| v4 (`AUDIT-V5-FINAL.md`) | Jun 14 | 3 new P1 bugs | A- | ✅ All fixed |
| v5 (`AUDIT-V6-FINAL.md`) | Jun 14 | 17 new issues | A | ✅ All fixed |

See `plans/` for full audit reports.

## License

MIT
