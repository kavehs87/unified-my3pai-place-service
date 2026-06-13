# Unified My3Pai Place Service

A provider-agnostic data store and query API for tourism and point-of-interest data. Ingests places from any source into a single normalized schema, then serves them through a fast, cached, spatially aware REST API.

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
| **Soft deletes** | Entities are deactivated (`is_active = false`) rather than destroyed. |

## Tech Stack

- **Python 3.12+** — FastAPI, async throughout
- **PostgreSQL + PostGIS** — Spatial indexing, full-text search, JSONB queries
- **Redis** — Response caching, rate limiting
- **SQLModel + asyncpg** — Type-safe ORM with raw SQL where it counts
- **Alembic** — Database migrations
- **structlog** — Structured JSON logging with request ID tracing
- **Prometheus** — Request metrics and cache hit/miss tracking
- **uv** — Lockfile-based package management

## Architecture

```
src/dmo/
├── main.py              # App factory, lifespan, middleware chain
├── config.py            # Settings via pydantic-settings
├── db.py                # Async connection pool + session factory
├── exceptions.py        # Structured error responses
├── logging.py           # structlog JSON renderer
├── metrics.py           # Prometheus counters/histograms
├── api/
│   ├── router.py        # REST endpoints (read + write)
│   ├── health.py        # /health with DB + Redis checks
│   └── metrics.py       # /metrics Prometheus endpoint
├── middleware/
│   ├── request_id.py    # X-Request-ID generation + 5xx logging
│   └── rate_limit.py    # Redis sliding-window rate limiter
├── models/
│   ├── database.py      # SQLModel tables (Entity, Media, Classification, Route)
│   └── schemas.py       # Pydantic request/response schemas
└── services/
    ├── cache.py         # Redis cache wrapper (MD5-hashed keys)
    ├── classifications.py  # Classifications list + categories
    ├── detail.py        # Detail query with asyncio.gather optimization
    ├── pagination.py    # Cursor encode/decode helpers
    ├── search.py        # Text search (pg_trgm) + filters
    ├── spatial.py       # Nearby (ST_DWithin) + map (ST_Intersects)
    └── write.py         # Create/update/delete + cache invalidation
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
| **Hours** | `is_free`, `is_open`, `opens_at`, `closes_at`, `opening_hours`, `recommended_season`, `business_status` |
| **Contact** | `phone`, `email`, `booking_link`, `menu_url`, `order_url`, `reservations_url` |
| **Pricing** | `currency`, `price_min`, `price_max`, `price_level` |
| **Accessibility** | `is_barrier_free`, `wheelchair_accessible` |
| **Popularity** | `is_featured`, `favorite_count`, `rating`, `reviews_count` |
| **Overflow** | `attributes` (JSONB) |
| **Lifecycle** | `is_active`, `imported_at`, `updated_at` |

**Indexes:** GIST on `location`, B-tree on `(lat, lon)`, GIN on `attributes`, GIN trgm on `name`, partial on `rating`, composite on `(source, is_active)`, `(place_type, is_active)`, `(country, is_active)`, `(collection_id, is_active)`.

### `media` — Images, videos, audio per entity

`id`, `entity_id` (FK CASCADE), `media_type`, `url`, `name`, `keywords`, `copyright_holder`, `publisher`, `width`, `height`, `encoding_format`, `sort_order`, `attributions` (JSONB), `poster_url`, `is_muted`

### `classifications` — Categorical tags per entity

`id`, `entity_id` (FK CASCADE), `category`, `value_code`, `value_title`

### `routes` — Geometries for trails and tours

`id`, `entity_id` (FK CASCADE), `geometry` (GEOMETRY), `elevation_profile` (JSONB), `fetched_at`

## API

### Read Endpoints

| Method | Path | Description | Cache |
|---|---|---|---|
| `GET` | `/search` | Full-text search with filters | 5 min |
| `GET` | `/nearby` | Proximity search (lat/lon/radius) | 5 min |
| `GET` | `/map` | Bounding-box spatial query | 5 min |
| `GET` | `/{source}/{source_id}` | Entity detail + media + classifications | 30 min |
| `GET` | `/classifications` | List classifications with filters | 5 min |
| `GET` | `/classifications/categories` | Distinct category values | 5 min |
| `GET` | `/health` | Health check (DB + Redis) | — |
| `GET` | `/metrics` | Prometheus metrics | — |

### Write Endpoints

| Method | Path | Description | Status |
|---|---|---|---|
| `POST` | `/entities` | Create entity | 201 |
| `POST` | `/entities/bulk` | Bulk upsert | 201 |
| `PUT` | `/{source}/{source_id}` | Update entity | 200 |
| `DELETE` | `/{source}/{source_id}` | Soft-delete entity | 200 |
| `POST` | `/media` | Add media to entity | 201 |
| `DELETE` | `/media/{media_id}` | Delete media | 200 |
| `POST` | `/classifications` | Add classification | 201 |

### Pagination

All list endpoints use **cursor-based pagination** (keyset pagination) for stable, performant results at any offset:

```json
{
  "results": [...],
  "total": 1250,
  "next_cursor": "eyJpZCI6ICIzZmE...IiwgInNvcnQiOiAiMjAyNS0wMS0wMSJ9",
  "has_more": true
}
```

### Error Responses

```json
{
  "error": "NotFound",
  "message": "Entity not found: google/ChIJ...",
  "code": 404,
  "request_id": "req_abc123"
}
```

## Caching

- **Redis** with MD5-hashed keys: `dmo:{endpoint}:{hash_of_params}`
- **TTLs:** 5 min (search/nearby/map/classifications), 30 min (detail), 60 s (open-status)
- **Fault-tolerant:** Cache failures pass through silently — no cascading failures
- **Invalidation:** Write operations clear `dmo:*` or entity-specific detail caches
- **Instrumented:** `cache_hits_total` and `cache_misses_total` Prometheus counters

## Scalability

| Feature | Benefit |
|---|---|
| **Async I/O** | Non-blocking DB queries, Redis calls, and HTTP responses |
| **Connection pooling** | Configurable pool (default 20 + 10 overflow) |
| **Cursor pagination** | No OFFSET/N degradation — O(1) page access |
| **GIST spatial index** | Fast Haversine and bounding-box queries |
| **pg_trgm GIN index** | Fuzzy text search without full PostgreSQL FTS overhead |
| **JSONB GIN index** | Queryable overflow attributes without schema changes |
| **Parallel detail queries** | `asyncio.gather` fetches entity, media, and classifications concurrently |
| **Rate limiting** | Redis sliding window (1000 req/60s default) |
| **Request timeouts** | Configurable timeout middleware (30s default, returns 504) |
| **Multi-worker** | Uvicorn with 4 workers in production |

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

The API will be available at `http://localhost:8000` with auto-generated docs at `/docs` (Swagger UI) and `/redoc` (ReDoc).

### Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/dmo` | Async DB connection |
| `DATABASE_URL_SYNC` | `postgresql+psycopg2://postgres:postgres@localhost:5432/dmo` | Sync DB connection (Alembic) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `CACHE_TTL` | `300` | Default cache TTL (seconds) |
| `RATE_LIMIT_ENABLED` | `true` | Enable/disable rate limiting |
| `RATE_LIMIT_MAX_REQUESTS` | `1000` | Max requests per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window (seconds) |
| `POOL_SIZE` | `20` | DB connection pool size |
| `MAX_OVERFLOW` | `10` | DB max overflow connections |
| `LOG_LEVEL` | `INFO` | Logging level |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins |
| `REQUEST_TIMEOUT_SECONDS` | `30.0` | Request timeout (seconds) |

## Testing

Tests run against a real PostgreSQL + PostGIS database (not SQLite) to ensure spatial query correctness.

```bash
# Run tests
uv run pytest tests/

# Lint
uv run ruff check src/ tests/
```

**Test coverage:** 70+ tests across search, spatial queries, detail, classifications, CRUD operations, error handling, caching, rate limiting, and health checks.

## Production Deployment

```bash
# Production stack with health checks and resource limits
docker compose -f docker-compose.prod.yml up -d
```

The Dockerfile uses a multi-stage build with `uv` for fast, lockfile-based dependency installation. The entrypoint runs Alembic migrations before starting Uvicorn with 4 workers.

## Query Patterns

```sql
-- Spatial proximity
ST_DWithin(location, ST_MakePoint(lon, lat, 4326)::geography, radius_m)

-- Bounding box
ST_Intersects(location, ST_MakeEnvelope(minLon, minLat, maxLon, maxLat, 4326)::geography)

-- JSONB containment
attributes @> '{"key": "value"}'

-- JSONB numeric sort
(attributes->>'distance_km')::numeric DESC

-- Text search (trigram)
name ILIKE '%query%'  -- backed by GIN gin_trgm_ops index

-- Array overlap
region_names && ARRAY['Switzerland', 'Zurich']
```

## License

MIT
