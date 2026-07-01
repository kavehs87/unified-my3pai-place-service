# Load Test Report Template

> Fill after each test suite run. Copy this file and populate with results.

## Test Metadata

| Field | Value |
|-------|-------|
| Date | YYYY-MM-DD |
| Environment | staging / prod |
| VM | `10.0.2.10` |
| Resources | N CPU / N GB RAM |
| Entity count | N,NNN,NNN |
| Rate limiting | ON / OFF |
| PostgreSQL config | work_mem=N, shared_buffers=N |
| Redis config | maxmemory=N, policy=xxx |
| API config | POOL_SIZE=N, MAX_OVERFLOW=N |
| k6 version | x.x.x |

## Executive Summary

- **Overall status:** PASS / FAIL / PARTIAL
- **Breaking point:** N concurrent users (VUs)
- **Max sustained RPS:** N (warm cache), N (cold cache)
- **Soak test result:** N min, N requests, N% failures
- **Key finding:** [one-line summary]

## Per-Scenario Results

| Scenario | VUs | Duration | Requests | Fail Rate | Avg | P50 | P95 | P99 | Status |
|----------|-----|----------|----------|-----------|-----|-----|-----|-----|--------|
| Warmup | | | | | | | | | |
| Cold cache | | | | | | | | | |
| Ramp | | | | | | | | | |
| Sustained | | | | | | | | | |
| Spatial stress | | | | | | | | | |
| Bulk single | | | | | | | | | |
| Bulk multi | | | | | | | | | |
| Write mixed | | | | | | | | | |
| Mixed read+write | | | | | | | | | |
| Stampede | | | | | | | | | |
| Timeout sat. | | | | | | | | | |
| Rate limit | | | | | | | | | |
| Soak | | | | | | | | | |

## Per-Endpoint Capacity Ceiling

| Endpoint | Traffic % | Warm P95 | Cold P95 | Max RPS | Bottleneck |
|----------|-----------|----------|----------|---------|------------|
| /search | | | | | |
| /nearby | | | | | |
| /map | | | | | |
| /{source}/{id} | | | | | |
| /classifications | | | | | |
| /classifications/categories | | | | | |
| /unified-categories | | | | | |

## Pass/Fail Checklist (§5.4)

| Check | Value | Threshold | Status |
|-------|-------|-----------|--------|
| P95 latency (read, warm) | | <500ms | |
| P95 latency (read, cold) | | <3s | |
| Error rate (non-429) | | <0.5% | |
| DB pool saturation | | <80% | |
| Statement timeouts | | None | |
| Memory growth (soak) | | <20% | |
| Stampede DB fetches | | <3 | |
| 504 response shape | | Valid JSON | |

## Production Launch Recommendation

- **Safe concurrent users:** N
- **Max sustained RPS:** N
- **Required config changes:** [list]
- **Known limitations:** [list]
- **Go/no-go decision:** GO / NO-GO

## Infrastructure Metrics (End of Test)

| Metric | Value |
|--------|-------|
| API memory | |
| Redis memory | |
| Redis keys | |
| DB connections | |
| Cache hit ratio | |

## Appendix: Raw Results

- k6 JSON output: `results/*.json`
- Query analysis: `results/query-analysis.txt`
- Log files: `results/*.log`
