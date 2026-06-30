# unified-my3pai-place-service

> Unified data store and query API for tourism/POI entities across multiple import sources.

**Status:** Data collection phase — not yet in production.

## Overview

Provider-agnostic PostgreSQL + PostGIS data store with a REST API for tourism, POI, and place data. Schema knows nothing about any data source — new sources require zero schema changes.

**This is a read-only data store + query API.** External importers write data. This project does NOT fetch, scrape, or import data.

### Data Inventory (Staging)

| Source | Entities | Classifications | Media | Notes |
|--------|----------|-----------------|-------|-------|
| osm | 786,654 | 0 | 0 | Rich `osm_*` attributes (100+ keys) |
| rexby | 124,490 | 0 | 0 | `rexby_*` attributes (31 keys) |
| tourpedia | 107,938 | 0 | 0 | `tourpedia_*` attributes, external links, photos |
| swiss_dmo | 8,177 | 52,463 | 13,020 | Only source with classifications + media |
| dzt | 76,386 | 0 | 0 | Empty attributes, poor data quality |
| **Total** | **1,103,645** | **52,463** | **13,020** | |

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (my3pai)                                                  │
│  - Calls public REST API                                           │
│  - No source-specific rules                                        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│  This Service                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Read API │  │ Write API│  │ Admin UI │  │ Admin Scripts    │   │
│  │ (public) │  │ (keyed)  │  │ (HTMX)   │  │ (auto-discover)  │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘   │
│       │              │              │                 │             │
│  ┌────▼──────────────▼──────────────▼─────────────────▼──────────┐  │
│  │  Services: search, spatial, detail, cache, write, pagination  │  │
│  └────────────────────────┬──────────────────────────────────────┘  │
│                           │                                         │
│  ┌────────────────────────▼──────────────────────────────────────┐  │
│  │  PostgreSQL 16 + PostGIS + Redis                              │  │
│  │  Tables: entities, media, classifications, routes             │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Tech Stack

Python 3.12+ | FastAPI | PostgreSQL 16 + PostGIS | asyncpg + SQLModel | Alembic | Redis | uv | structlog | Prometheus

## Quick Start

```bash
# Clone and install
uv sync

# Configure environment
cp .env.example .env
# Edit DATABASE_URL, API_KEY, REDIS_URL

# Start development server
uv run fastapi dev src/dmo/main.py

# Run tests
uv run pytest tests/

# Lint and format
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

## API Endpoints

### Read (public)

```
GET  /search?q=&source=&place_type=&country=&page_size=&cursor=
GET  /nearby?lat=&lon=&radius_km=&source=&place_type=&page_size=&cursor=
GET  /map?bbox=&source=&place_type=&page_size=&cursor=
GET  /{source}/{source_id}
GET  /classifications/categories
GET  /classifications?entity_id=&category=&value_code=&page_size=&cursor=
```

### Write (requires `X-API-Key` header)

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
GET  /health    — DB + Redis health (200 or 503)
GET  /metrics   — Prometheus metrics
```

### Admin UI

```
GET  /admin/              — dashboard (HTTP Basic Auth)
GET  /admin/entities      — entity browser
GET  /admin/scripts       — script execution center
GET  /admin/settings      — configuration viewer
```

## Admin Scripts

Auto-discovered management scripts for data quality, enrichment, and maintenance. Each script runs via the admin UI or CLI with dry-run support, batch processing, and resumable pagination.

| Script | Category | Purpose |
|--------|----------|---------|
| `normalize_place_types` | Fix | Normalize inconsistent place_type values |
| `fix_description_format` | Fix | Fix description_format field mismatches |
| `heal_missing_coordinates` | Heal | Geocode entities missing lat/lon |
| `heal_missing_country` | Heal | Infer country from coordinates |
| `enrich_descriptions` | Enrich | Generate descriptions from name + attributes |
| `enrich_from_wikidata` | Enrich | Enrich entities via Wikidata API |
| `score_osm_entities` | Enrich | Quality score OSM entities for prioritization |

## Database Schema

### Core Tables

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `entities` | Main place data | id, source, source_id, name, place_type, location, country, region_names, attributes |
| `media` | Photos, videos | id, entity_id, media_type, url, thumbnail_url, caption |
| `classifications` | Categories, tags | id, entity_id, category_class, value_code, value_label |
| `routes` | Routes and tours | id, entity_id, name, route_type, distance_km, duration_min, elevation_gain_m, gpx_xml |

### Design Principles

1. **No provider prefixes** — no `google_*`, `dzt_*`, `rexby_*` columns
2. **`source` is VARCHAR** — new data source = zero schema changes
3. **`place_type` is VARCHAR** — new types emerge from data
4. **`attributes` JSONB is overflow** — provider-specific fields live here
5. **`source` + `source_id` unique** — same entity from different sources = separate rows

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | *(required)* | Async DB connection (asyncpg) |
| `DATABASE_URL_SYNC` | *(required)* | Sync DB connection for Alembic |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `API_KEY` | *(required)* | API key for write endpoints |
| `CACHE_TTL` | `300` | Default cache TTL in seconds |
| `RATE_LIMIT_ENABLED` | `true` | Enable/disable rate limiting |
| `RATE_LIMIT_MAX_REQUESTS` | `1000` | Max requests per window per IP |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window in seconds |
| `TRUST_PROXY_HEADERS` | `true` | Use `X-Forwarded-For` for client IP |
| `POOL_SIZE` | `10` | DB connection pool size per worker |
| `QUERY_TIMEOUT_SECONDS` | `10.0` | Read query timeout |
| `REQUEST_TIMEOUT_SECONDS` | `30.0` | HTTP request timeout |
| `LOG_LEVEL` | `INFO` | Logging level |

## Deployment

### Staging

```bash
# SSH tunnel to staging DB
ssh -L 5432:db:5432 staging

# Deploy to staging VM (10.0.2.10)
# Uses rsync + docker compose
# See .opencode/skills/deploy-staging/ for full workflow
```

### Production

```bash
docker compose -f docker-compose.prod.yml up -d
```

Multi-stage Docker build with `uv` lockfile, Alembic migrations, Uvicorn (4 workers), health checks.

## Testing

```bash
uv run pytest tests/          # 231 tests, 0 lint errors
uv run ruff check src/ tests/ # lint
uv run ruff format src/ tests/ # format
```

Tests use real PostGIS database (not SQLite). Cache disabled in tests via autouse fixture.

## Project Structure

```
src/dmo/
├── main.py              # FastAPI app, lifespan, middleware
├── config.py            # Settings (pydantic BaseSettings)
├── db.py                # Connection pool, session factory
├── logging.py           # structlog (JSON renderer)
├── metrics.py           # Prometheus counters/histograms
├── exceptions.py        # AppError, global exception handlers
├── api/
│   ├── router.py        # All REST endpoints
│   ├── health.py        # /health endpoint
│   └── metrics.py       # /metrics endpoint
├── admin/
│   ├── router.py        # Admin UI routes (HTMX + Jinja2)
│   └── templates/       # Jinja2 templates
├── admin_scripts/
│   ├── base.py          # AdminScript base class
│   ├── registry.py      # Auto-discovery via pkgutil
│   └── *.py             # Individual scripts
├── middleware/
│   ├── request_id.py    # X-Request-ID generation/validation
│   └── rate_limit.py    # Per-IP Redis sliding window
├── models/
│   ├── database.py      # SQLModel tables
│   └── schemas.py       # Pydantic request/response schemas
└── services/
    ├── cache.py         # SHA-256-hashed Redis cache
    ├── classifications.py  # Classifications + categories
    ├── detail.py        # Detail query + open-status merge
    ├── pagination.py    # Cursor encode/decode
    ├── search.py        # Text search + single-pass count
    ├── spatial.py       # Nearby + map queries
    └── write.py         # Create/update/delete + cache invalidation
```

## OpenAPI Docs

```bash
uv run python scripts/export-openapi.py
npx redocly build-docs docs/openapi.json -o docs/index.html --config docs/redocly.yaml
```

## Load Test Benchmarks

Phase 1 results on staging VM (12 CPU / 6GB RAM / 4 Uvicorn workers / 60 DB connections / 1.2M entities). Run 2026-06-30.

### Read-Only Scenarios

| Scenario | VUs | Duration | Iterations | Failures | Avg Latency | P95 | Notes |
|----------|-----|----------|------------|----------|-------------|-----|-------|
| Read ramp | 50→800 | 15m | 129,702 | 14.17% | — | — | Breaking point ~400 VUs |
| Read sustained | 200 | 5m | 18,723 | — | — | — | Warm cache |
| Cold cache | 50 | 2m | 7,473 | 16.27% | 831ms | 9.12s | Cache flushed before run |
| Stampede | 100 | 2m | 2,402 | 0% | 2.86s | 5.05s | SET NX lock working |
| Timeout sat. | ramp→100 | — | 628 | 58.12% | 958ms | 1.07s | Deliberate 10s statement timeout |

### Write Scenarios

| Scenario | VUs | Duration | Iterations | Failures | Avg Latency | Notes |
|----------|-----|----------|------------|----------|-------------|-------|
| Bulk single source | 5 | 2m | — | — | — | 100 entities/batch |
| Bulk multi source | 8 | 2m | — | — | — | Per-VU source isolation |
| Write mixed | 2 | 5m | 5,124 | 0% | 117ms | Create/update/media/classification |

### Mixed Workload

| Scenario | Config | Iterations | Failures | Avg Latency | P95 | Notes |
|----------|--------|------------|----------|-------------|-----|-------|
| Mixed read+write | 200 read + 2 bulk | 20,195 | 21.17% | 5.98s | 16.01s | Cache thrashing from parallel writes |

### Rate Limiting

| Scenario | Config | Requests | 200 OK | 429 Limited | Avg Latency | P95 | Notes |
|----------|--------|----------|--------|-------------|-------------|-----|-------|
| Single IP (50 RPS) | 1 VU, 50 RPS, 2000 iters | 2,000 | 1,000 | 1,000 | 4.4ms | 6.75ms | 429s start at request 1001 (~20s) |
| Burst | 10 VUs, 5s | 7,645 | 1,002 | 6,643 | 6.45ms | 13.58ms | Remaining window capacity used |
| Multi IP | 20 VUs, 2min | 33,682 | ~20,000 | ~13,682 | 21ms | 47ms | Per-IP isolation working |

### Spatial Stress

| Scenario | VUs | Duration | Iterations | Requests | Failures | Avg Latency | P95 |
|----------|-----|----------|------------|----------|----------|-------------|-----|
| Dense bbox | 50 | 5m | 12,632 | 37,896 | 0% | 363ms | 649ms |

### Soak Test (Stability)

| Scenario | VUs | Duration | Iterations | Requests | Failures | Avg Latency | P95 |
|----------|-----|----------|------------|----------|----------|-------------|-----|
| Sustained (Phase 2) | 50 | 30m | 124,181 | 124,181 | 16.29% | 625ms | 5.42s |
| Sustained (Phase 3) | 10 | 30m | 33,752 | 101,256 | 0.00% | 10ms | 10ms |

### Environment

- Rate limiting: disabled (re-enabled for rate limit tests)
- pg_stat_statements: enabled
- Cache: Redis, 5min TTL (search), 30min TTL (detail), 60s TTL (open-status)
- Backup taken before test: `backups/pre-phase2-loadtest/`
- Cleanup: 106,564 test entities deleted post-run

## Phase 3 — Optimization Results

Run 2026-06-30 on staging VM (10.0.2.10). All optimizations are config/index changes only (zero code modifications).

### Optimizations Applied

| Component | Setting | Before | After |
|-----------|---------|--------|-------|
| PostgreSQL | `work_mem` | 4MB | 32MB |
| PostgreSQL | `shared_buffers` | 128MB | 1.5GB |
| PostgreSQL | `maintenance_work_mem` | 64MB | 512MB |
| PostgreSQL | `random_page_cost` | 4.0 | 1.1 |
| Redis | `maxmemory-policy` | noeviction | allkeys-lru |
| Redis | `maxmemory` | unlimited | 2GB |
| Redis | `timeout` | 0 | 300s |
| API | `POOL_SIZE` | 10 | 20 |
| API | `MAX_OVERFLOW` | 5 | 10 |
| API | `QUERY_TIMEOUT_SECONDS` | 10 | 30 |
| Indexes | Partial (active only) | N/A | 4 indexes (531MB) |

### Before/After Comparison

| Test | Metric | Phase 2 Baseline | Phase 3 Optimized | Change |
|------|--------|-----------------|-------------------|--------|
| Cold Cache (50 VUs, 2m) | Failure rate | 16.27% | **0.22%** | ✅ 98.6% reduction |
| Cold Cache (50 VUs, 2m) | P95 latency | 9.12s | 9.67s | More queries complete |
| Spatial Stress (50 VUs, 5m) | P95 latency | 649ms | **986ms** | Under concurrent load |
| Spatial Stress (50 VUs, 5m) | Failure rate | 0% | **0%** | ✅ Maintained |
| Mixed Read+Write (1 VU, 10m) | Failure rate | N/A | **0%** | ✅ New test |
| Soak (10 VUs, 30m) | Failure rate | 16.29% | **0.00%** | ✅ 101K requests, 0 failures |
| Soak (10 VUs, 30m) | P95 latency | 5.42s | **0.01s** | ✅ Warm cache stable |

### Indexes Created

| Index | Size | Purpose |
|-------|------|---------|
| `idx_entities_name_trgm_active` | 91MB | Trigram search on active entities |
| `idx_entities_summary_trgm_active` | 146MB | Trigram search on summary |
| `idx_entities_location_active` | 94MB | Spatial queries on active entities |
| `idx_entities_location_active_type` | 200MB | Covering index for spatial + type filter |

### Root Cause Analysis

**Cold-cache failures (16.27% → 0.22%):** Primary cause was insufficient DB pool (10 connections for 50 VUs) and short query timeout (10s). Doubling pool size and tripling timeout eliminated connection exhaustion. New partial indexes reduce scan volume for active entities.

**Spatial P95 increase (649ms → 986ms):** Spatial queries hit the larger covering index under concurrent load. Still within acceptable range (<1s P95, 0% failures).

### Running Tests

```bash
# Full suite (requires staging VM at 10.0.2.10)
./loadtest/run_all.sh

# Individual scenario
k6 run loadtest/search.js --env BASE_URL=http://10.0.2.10:8000

# Post-test analysis
python scripts/analyze_queries.py
```

```bash
# Full suite (requires staging VM at 10.0.2.10)
./loadtest/run_all.sh

# Individual scenario
k6 run loadtest/search.js --env BASE_URL=http://10.0.2.10:8000

# Post-test analysis
python scripts/analyze_queries.py
```

## Plans

See `plans/` for design documents, audit reports, and implementation plans.
