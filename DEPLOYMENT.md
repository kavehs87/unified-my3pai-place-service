# Production Deployment Guide

Unified My3pai Place Service — FastAPI + PostGIS + Redis

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Hardware Requirements](#2-hardware-requirements)
3. [Prerequisites](#3-prerequisites)
4. [Deployment](#4-deployment)
5. [Configuration](#5-configuration)
6. [Database Setup](#6-database-setup)
7. [Service Details](#7-service-details)
8. [Monitoring & Health](#8-monitoring--health)
9. [Scaling](#9-scaling)
10. [Backup & Recovery](#10-backup--recovery)
11. [Security Checklist](#11-security-checklist)
12. [Troubleshooting](#12-troubleshooting)
13. [Rollback](#13-rollback)

---

## 1. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                         Host VM                         │
│              4 vCPU / 4 GB RAM / SSD                    │
│                                                         │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐            │
│  │   API    │──▶│  PostGIS  │   │  Redis   │            │
│  │  (3G)    │◀──│  (16-3.4) │   │  (1.5G)  │            │
│  │ FastAPI  │   │  pgdata   │   │  RDB     │            │
│  │ Uvicorn  │   │  /5432    │   │  /6379   │            │
│  │ 4 workers│   │            │   │          │            │
│  │ /8000    │   │            │   │          │            │
│  └──────────┘   └──────────┘   └──────────┘            │
│                                                         │
│  Volumes: pgdata, redis_data, admin_settings            │
└─────────────────────────────────────────────────────────┘
```

Three services, three persistent volumes, single host.

### Data Flow

1. Client → API (`:8000`) → Redis cache (hit → return, miss → DB)
2. API → PostGIS (`:5432`) → Query → Cache result → Return
3. Write endpoints → PostGIS → Invalidate cache (7 SCANs per entity)

---

## 2. Hardware Requirements

| Resource | Minimum | Recommended | Notes |
|----------|---------|-------------|-------|
| vCPU | 4 | 4-8 | Spatial queries are CPU-bound |
| RAM | 4 GB | 4-8 GB | API 3G + Redis 1.5G + DB ~1.5G |
| Disk | 50 GB SSD | 100 GB SSD | pgdata grows with entity count |
| Network | 1 Gbps | 1 Gbps | Low bandwidth (JSON responses) |

### Resource Allocation

| Service | CPU Limit | Memory Limit | Notes |
|---------|-----------|--------------|-------|
| API | 4 cores | 3 GB | 4 Uvicorn workers |
| Redis | — | 1.5 GB | `maxmemory 1gb` + 0.5G overhead |
| PostGIS | — | ~1.5 GB | `shared_buffers=1.5GB` |

### Capacity (4 CPU / 3G RAM, post-optimization)

| Metric | Safe Zone | Warning | Critical |
|--------|-----------|---------|----------|
| Concurrent users | ≤50 | 50-130 | >130 |
| Sustained RPS (warm cache) | ≤200 | 200-400 | >400 |
| Sustained RPS (cold cache) | ≤100 | 100-200 | >200 |
| Error rate | <0.1% | 0.1-0.5% | >0.5% |
| P95 latency (warm) | <50ms | 50-500ms | >500ms |
| P95 latency (cold) | <500ms | 500ms-3s | >3s |

**Breaking point:** ~130 concurrent users (DB pool exhaustion).

---

## 3. Prerequisites

### On the host VM

```bash
# Docker (20.10+)
curl -fsSL https://get.docker.com | sh

# Docker Compose (v2)
# Included with Docker Desktop or install separately

# Verify
docker --version
docker compose version
```

### On the build machine

```bash
# Git
git clone https://github.com/kavehs87/unified-my3pai-place-service.git
cd unified-my3pai-place-service
```

---

## 4. Deployment

### 4.1 Clone and configure

```bash
# On the host VM
mkdir -p /root/ups
cd /root/ups

# Copy project files (from build machine or git clone)
# scp -r <source>/unified-my3pai-place-service/* /root/ups/

# Create .env
cp .env.example .env
nano .env  # Edit production values (see §5)
```

### 4.2 Build and start

```bash
cd /root/ups

# Build all services
docker compose -f docker-compose.prod.yml build

# Start in detached mode
docker compose -f docker-compose.prod.yml up -d

# Watch startup logs
docker compose -f docker-compose.prod.yml logs -f api
```

### 4.3 Verify

```bash
# Health check
curl http://localhost:8000/health
# Expected: {"status":"ok","components":{"database":"up","redis":"up"}}

# Service status
docker ps
# Expected: 3 containers, all "healthy"

# Entity count
docker exec ups-db-1 psql -U postgres -d dmo -t \
  -c "SELECT count(*) FROM entities WHERE is_active = true;"
```

### 4.4 First-time migration

On first deploy, the entrypoint runs `alembic upgrade head` automatically. To run migrations manually:

```bash
docker exec -it ups-api-1 alembic upgrade head
```

---

## 5. Configuration

### 5.1 `.env` file

```bash
# ── Database ─────────────────────────────────────────────
DATABASE_URL=postgresql+asyncpg://postgres:<password>@db:5432/dmo
DATABASE_URL_SYNC=postgresql+psycopg2://postgres:<password>@db:5432/dmo
REDIS_URL=redis://redis:6379/0

# ── Connection Pool (tuned for 4 CPU / 3G RAM) ──────────
POOL_SIZE=20
MAX_OVERFLOW=10
QUERY_TIMEOUT_SECONDS=30

# ── Cache ────────────────────────────────────────────────
CACHE_TTL=300

# ── Rate Limiting (ENABLE in production) ────────────────
RATE_LIMIT_ENABLED=true
RATE_LIMIT_MAX_REQUESTS=1000
RATE_LIMIT_WINDOW_SECONDS=60

# ── Security ─────────────────────────────────────────────
API_KEY=<production-secret-key>
ADMIN_USERNAME=<admin-user>
ADMIN_PASSWORD=<admin-password>
ALLOWED_ORIGINS=<production-domain>

# ── Logging ──────────────────────────────────────────────
LOG_LEVEL=WARNING

# ── Advanced (usually leave at defaults) ────────────────
REQUEST_TIMEOUT_SECONDS=30
SLOW_REQUEST_THRESHOLD_MS=500
TRUST_PROXY_HEADERS=true
CACHE_DEBUG=false
```

### 5.2 Config changes vs. defaults

| Setting | Default | Production | Reason |
|---------|---------|------------|--------|
| `POOL_SIZE` | 10 | **20** | 50 VUs need 20+ connections |
| `MAX_OVERFLOW` | 5 | **10** | Burst traffic during cache misses |
| `QUERY_TIMEOUT_SECONDS` | 10 | **30** | Spatial queries on large bboxes |
| `RATE_LIMIT_ENABLED` | true | **true** | Must be enabled |
| `LOG_LEVEL` | INFO | **WARNING** | Reduce I/O overhead |
| Redis `maxmemory` | 0 (unlimited) | **1gb** | Prevent OOM kills at peak |
| Redis `maxmemory-policy` | noeviction | **allkeys-lru** | Graceful degradation |

### 5.3 `docker-compose.prod.yml` resource limits

```yaml
api:
  deploy:
    resources:
      limits:
        cpus: "4"
        memory: 3G

redis:
  deploy:
    resources:
      limits:
        memory: 1.5G
```

### 5.4 PostgreSQL tuning

PostgreSQL uses default settings from the `postgis/postgis:16-3.4` image. For a 4 GB RAM server, consider adding a custom `postgresql.conf` volume:

```ini
# Recommended for 4 GB RAM single-instance
shared_buffers = 1.5GB
work_mem = 32MB
maintenance_work_mem = 512MB
effective_cache_size = 3GB
max_connections = 100
random_page_cost = 1.1          # SSD
wal_buffers = 64MB
checkpoint_completion_target = 0.9
```

Mount as:
```yaml
db:
  volumes:
    - ./postgresql.conf:/etc/postgresql/postgresql.conf
```

---

## 6. Database Setup

### 6.1 Image

`postgis/postgis:16-3.4` — PostgreSQL 16 with PostGIS 3.4 extension.

### 6.2 Required extensions

The image enables PostGIS by default. Trigram indexes require `pg_trgm`:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS postgis;
```

These are created by the initial migration (`001_initial_schema.py`).

### 6.3 Key indexes

| Index | Type | Purpose |
|-------|------|---------|
| `idx_entities_location` | GiST | Spatial queries (`ST_Intersects`, `ST_DWithin`) |
| `idx_entities_lat_lon` | B-tree | Bounding box filters |
| `idx_entities_name_trgm` | GIN (trigram) | Fuzzy name search |
| `idx_entities_summary_trgm` | GIN (trigram) | Fuzzy summary search (opt-in) |
| `idx_entities_source` | B-tree (partial) | Source + is_active filtering |
| `idx_entities_type` | B-tree (partial) | Place type filtering |
| `idx_entities_attributes` | GIN | JSONB attribute queries |
| `idx_entities_source_unique` | Unique | Deduplication (source + source_id) |
| `idx_entities_country` | B-tree (partial) | Country filtering |
| `idx_entities_rating` | B-tree (partial) | Rating queries |

### 6.4 `pg_stat_statements`

Enable for query analysis:

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
```

Then use `scripts/analyze_queries.py` to identify slow queries.

---

## 7. Service Details

### 7.1 API (FastAPI + Uvicorn)

| Property | Value |
|----------|-------|
| Image | Built from `Dockerfile` (Python 3.12-slim) |
| Workers | 4 (Uvicorn) |
| Port | 8000 |
| Health | `GET /health` |
| Metrics | `GET /metrics` (Prometheus) |
| Restart | `unless-stopped` |

#### Key endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (DB + Redis status) |
| GET | `/search?q=...` | Full-text + spatial search |
| GET | `/nearby?lat=...&lon=...` | Nearby entities by distance |
| GET | `/map?bbox=...` | Map tile query (spatial) |
| GET | `/{source}/{id}` | Entity detail |
| GET | `/classifications` | Classification taxonomy |
| GET | `/classifications/categories` | Category list |
| GET | `/unified-categories` | Unified category taxonomy |
| POST | `/entities/bulk` | Bulk upsert (advisory lock) |
| POST | `/entities` | Single entity create |
| PUT | `/{source}/{id}` | Single entity update |
| POST | `/media` | Media attachment |
| POST | `/classifications` | Classification assignment |

### 7.2 Redis

| Property | Value |
|----------|-------|
| Image | `redis:7-alpine` |
| Port | 6379 |
| Max memory | 1 GB |
| Eviction policy | `allkeys-lru` |
| Persistence | RDB (900s/1 change, 300s/10 changes) |
| Volume | `redis_data` |

Redis is used for response caching (TTL-based) and rate limiting (sliding window with sorted sets).

### 7.3 Volumes

| Volume | Purpose | Persistence |
|--------|---------|-------------|
| `pgdata` | PostgreSQL data directory | **Critical** — contains all entity data |
| `redis_data` | Redis RDB snapshots | Recovery (cache rebuilds on restart) |
| `admin_settings` | Admin panel settings | Configuration |

---

## 8. Monitoring & Health

### 8.1 Health checks

```bash
# API health (checks DB + Redis connectivity)
curl http://localhost:8000/health
# {"status":"ok","components":{"database":"up","redis":"up"}}

# Docker health
docker ps
# Look for "healthy" in STATUS column

# Individual service health
docker exec ups-db-1 pg_isready -U postgres
docker exec ups-redis-1 redis-cli ping
```

### 8.2 Prometheus metrics

```bash
curl http://localhost:8000/metrics
```

Key metrics to monitor:
- `http_request_duration_seconds` — Request latency
- `http_requests_total` — Request count by endpoint/status
- `process_resident_memory_bytes` — API memory usage

### 8.3 Log levels

| Level | Use case |
|-------|----------|
| `DEBUG` | Development, debugging |
| `INFO` | Staging, detailed audit trail |
| `WARNING` | **Production** (default) |
| `ERROR` | Minimal — only errors |

Set via `LOG_LEVEL` in `.env`.

### 8.4 Recommended monitoring

```bash
# Container resource usage
docker stats --no-stream

# Disk usage
docker system df

# PostgreSQL connection count
docker exec ups-db-1 psql -U postgres -d dmo -t \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname='dmo';"

# Redis memory
docker exec ups-redis-1 redis-cli INFO memory | grep used_memory_human

# Redis key count
docker exec ups-redis-1 redis-cli DBSIZE
```

---

## 9. Scaling

### 9.1 Horizontal scaling (API)

The API can run multiple replicas behind a load balancer. Redis and PostGIS are shared state.

```yaml
api:
  deploy:
    replicas: 2
    resources:
      limits:
        cpus: "2"       # Halve per replica
        memory: 2G
```

**Caveats:**
- Rate limiting is per-instance (each replica has its own Redis key per IP)
- Cache invalidation is shared (Redis SCAN invalidates for all replicas)

### 9.2 Vertical scaling

| Upgrade | Impact |
|---------|--------|
| More CPU | Spatial queries faster, higher VU ceiling |
| More RAM | Larger DB shared_buffers, more cache |
| Faster SSD | Lower query latency, faster WAL writes |

### 9.3 Beyond 130 concurrent users

At >130 VUs, the DB pool becomes the bottleneck. Options:

1. **Increase pool size** — `POOL_SIZE=30`, `MAX_OVERFLOW=15` (requires more RAM)
2. **Add PgBouncer** — Connection pooling in front of PostGIS
3. **Read replica** — Route read traffic to a replica
4. **More hardware** — 8 CPU / 8 GB RAM pushes ceiling to ~250 VUs

### 9.4 Known limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| Spatial queries CPU-bound | P95 2,691ms at 50 VUs | Front-end bbox filtering |
| DB pool ceiling at ~130 VUs | Errors above 130 concurrent users | Increase POOL_SIZE or add PgBouncer |
| Same-source writes serialized | ~1 batch/s per source | Distribute imports across sources |
| No read replica | All reads hit primary DB | Add read replica if scaling beyond 200 VUs |
| Redis eviction under peak | Cache misses at 800 VUs | Acceptable — degrades gracefully |

---

## 10. Backup & Recovery

### 10.1 Database backup

```bash
# Full backup
docker exec ups-db-1 pg_dump -U postgres dmo > dmo-backup-$(date +%F).sql

# Compressed backup
docker exec ups-db-1 pg_dump -U postgres -Fc dmo > dmo-backup-$(date +%F).dump

# Schema only
docker exec ups-db-1 pg_dump -U postgres -s dmo > dmo-schema-$(date +%F).sql
```

### 10.2 Database restore

```bash
# From SQL dump
cat dmo-backup.sql | docker exec -i ups-db-1 psql -U postgres dmo

# From custom format
docker exec -i ups-db-1 pg_restore -U postgres -d dmo --clean --if-exists < dmo-backup.dump
```

### 10.3 Redis backup

Redis RDB snapshots are written automatically to `/data` (mounted as `redis_data` volume):

```bash
# Trigger manual snapshot
docker exec ups-redis-1 redis-cli BGSAVE

# Backup the volume
docker run --rm -v ups_redis_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/redis-backup-$(date +%F).tar.gz -C /data .
```

### 10.4 Volume backup

```bash
# Backup all volumes
docker run --rm -v ups_pgdata:/pgdata -v ups_redis_data:/redis -v $(pwd):/backup alpine \
  tar czf /backup/volumes-$(date +%F).tar.gz -C / pgdata redis

# Restore
docker run --rm -v ups_pgdata:/pgdata -v $(pwd):/backup alpine \
  sh -c "rm -rf /pgdata/* && tar xzf /backup/volumes-*.tar.gz -C /"
```

### 10.5 Recovery procedures

| Scenario | Recovery |
|----------|----------|
| API crash | `docker compose -f docker-compose.prod.yml up -d api` (auto-restarts) |
| Redis crash | `docker compose -f docker-compose.prod.yml up -d redis` (RDB auto-loads) |
| DB crash | `docker compose -f docker-compose.prod.yml up -d db` (WAL auto-recovers) |
| Full host reboot | `docker compose -f docker-compose.prod.yml up -d` (all auto-recover) |
| Power loss | Services auto-recover via WAL/RDB; verify with `/health` |
| Data corruption | Restore from `pg_dump` backup |

### 10.6 Power outage behavior (tested)

After unclean shutdown:
- **PostgreSQL**: Recovers via WAL replay, zero data loss
- **Redis**: Loads RDB snapshot from disk, cache rebuilds on demand
- **API**: Restarts once dependencies are healthy

---

## 11. Security Checklist

- [ ] **Change default credentials** — `POSTGRES_PASSWORD`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`
- [ ] **Set `API_KEY`** — Use a strong random key
- [ ] **Set `ALLOWED_ORIGINS`** — Restrict CORS to production domain
- [ ] **Enable rate limiting** — `RATE_LIMIT_ENABLED=true`
- [ ] **Restrict port exposure** — Don't expose `:5432` or `:6379` to the internet
- [ ] **Use HTTPS** — Place behind reverse proxy (nginx, Caddy, or cloud LB)
- [ ] **Firewall rules** — Allow only `:8000` (or `:443`) from external
- [ ] **Regular backups** — Schedule `pg_dump` daily
- [ ] **Monitor logs** — Check for unusual patterns
- [ ] **Keep images updated** — Rebuild periodically for security patches

### Port exposure

```yaml
# Production: expose only API port, not DB or Redis
api:
  ports:
    - "8000:8000"
  # OR behind reverse proxy:
  # ports: []  # No host port, only internal network

db:
  ports: []  # Remove or comment out — internal only

redis:
  ports: []  # Remove or comment out — internal only
```

---

## 12. Troubleshooting

### 12.1 Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| API can't connect to DB | DB not healthy yet | `docker compose up -d` waits for healthcheck |
| Redis OOM killed | No maxmemory limit | Redis configured with `maxmemory 1gb` in compose |
| Slow spatial queries | Large bbox or no index | Check `idx_entities_location` exists |
| 504 Gateway Timeout | Query exceeds timeout | Increase `QUERY_TIMEOUT_SECONDS` |
| 500 on rate limit | Old code (pre-fix) | Deploy latest `rate_limit.py` |
| Cache not working | Redis down or wrong URL | Check `REDIS_URL` in `.env` |
| Migration fails | Alembic version mismatch | `docker exec ups-api-1 alembic current` |

### 12.2 Debug commands

```bash
# Check container logs
docker compose -f docker-compose.prod.yml logs --tail=100 api
docker compose -f docker-compose.prod.yml logs --tail=100 db
docker compose -f docker-compose.prod.yml logs --tail=100 redis

# Check environment variables
docker exec ups-api-1 printenv

# Check PostgreSQL connections
docker exec ups-db-1 psql -U postgres -d dmo -c \
  "SELECT count(*) as total, state FROM pg_stat_activity WHERE datname='dmo' GROUP BY state;"

# Check Redis memory
docker exec ups-redis-1 redis-cli INFO memory | grep -E "used_memory_human|maxmemory_human"

# Check disk usage
docker system df
docker exec ups-db-1 du -sh /var/lib/postgresql/data

# Check index usage
docker exec ups-db-1 psql -U postgres -d dmo -c \
  "SELECT indexrelname, idx_scan, idx_tup_read FROM pg_stat_user_indexes WHERE schemaname='public' ORDER BY idx_scan DESC;"

# Run migrations manually
docker exec -it ups-api-1 alembic upgrade head

# Rollback last migration
docker exec -it ups-api-1 alembic downgrade -1
```

### 12.3 Restart procedures

```bash
# Restart single service
docker compose -f docker-compose.prod.yml restart api

# Rebuild and restart (after code changes)
docker compose -f docker-compose.prod.yml build api
docker compose -f docker-compose.prod.yml up -d api

# Full restart
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d

# Force clean rebuild
docker compose -f docker-compose.prod.yml down --rmi local
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d
```

---

## 13. Rollback

### 13.1 Code rollback

```bash
# On build machine
cd /root/project-ups
git log --oneline -10  # Find the commit to roll back to
git checkout <commit-hash>

# Push to host
scp -r src/ Dockerfile pyproject.toml uv.lock entrypoint.sh \
  root@<host>:/root/ups/

# Rebuild on host
ssh root@<host> 'cd /root/ups && docker compose -f docker-compose.prod.yml build api && docker compose -f docker-compose.prod.yml up -d api'
```

### 13.2 Database rollback

```bash
# Downgrade last migration
docker exec -it ups-api-1 alembic downgrade -1

# Downgrade to specific version
docker exec -it ups-api-1 alembic downgrade <revision>

# Restore from backup
docker exec ups-db-1 psql -U postgres -d dmo -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
cat dmo-backup.sql | docker exec -i ups-db-1 psql -U postgres dmo
```

### 13.3 Configuration rollback

```bash
# .env changes take effect on restart
docker compose -f docker-compose.prod.yml down api
# Edit .env
docker compose -f docker-compose.prod.yml up -d api
```

---

## Appendix A: Entity count milestones

| Entity count | Observed behavior | Notes |
|-------------|-------------------|-------|
| ~1.2M | Warm cache P95: 10ms, Cold cache P95: 87ms | Current production state |
| >2M | Monitor spatial query times | Index performance degrades linearly |
| >5M | Consider partitioning | Evaluate range partitioning by country |

## Appendix B: Load test results summary

See `README.md` §Phase 2, §Phase 2 Reduced, §Phase 3, §Phase 4 for detailed results.

Key takeaways:
- **Soak test** (50 VUs, 120 min): 0% failures, P95 7.32ms
- **Breaking point**: ~130 concurrent users (DB pool exhaustion)
- **Peak test** (800 VUs, 5 min): 0.52% failures (after Redis fix)
- **Rate limiter**: Returns 429 with proper JSON body + Retry-After header

## Appendix C: File layout

```
/root/ups/                          # Production directory
├── docker-compose.prod.yml         # Service definitions
├── .env                            # Configuration (gitignored)
├── Dockerfile                      # API image build
├── pyproject.toml                  # Python dependencies
├── uv.lock                         # Locked dependencies
├── entrypoint.sh                   # Migration + startup
├── src/dmo/                        # Application code
│   ├── main.py                     # FastAPI app entry
│   ├── config.py                   # Settings
│   ├── middleware/                 # Rate limiting, CORS, etc.
│   ├── api/                        # Route handlers
│   └── ...
├── migrations/                     # Alembic migrations
├── alembic.ini                     # Migration config
└── loadtest/                       # k6 test scripts
```
