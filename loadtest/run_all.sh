#!/usr/bin/env bash
# loadtest/run_all.sh — Orchestrates full load test suite in order (§5.2).
# Runs on the workstation, targets staging at 10.0.2.10.
# Usage: ./loadtest/run_all.sh [--dry-run]
#
# Prerequisites (set on staging VM before running):
#   export RATE_LIMIT_ENABLED=false
#   CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
#   Patch scripts/analyze_queries.py (§2.1)

set -euo pipefail

STAGING_HOST="10.0.2.10"
BASE_URL="${BASE_URL:-http://${STAGING_HOST}:8000}"
API_KEY="${API_KEY:-mytestkey}"
RESULTS_DIR="$(cd "$(dirname "$0")/.." && pwd)/results"
LOADTEST_DIR="$(cd "$(dirname "$0")" && pwd)"
REST_DURATION=120  # 2 min rest between scenarios

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
fi

run() {
  local desc="$1"
  shift
  echo ""
  echo "============================================================"
  echo "  $desc"
  echo "============================================================"
  echo "  Command: $@"
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "  [DRY RUN — skipping]"
    return
  fi
  "$@"
  echo "  ✓ Done"
}

rest() {
  if [[ "$DRY_RUN" == "true" ]]; then
    return
  fi
  echo "  ⏸ Resting ${REST_DURATION}s..."
  sleep "$REST_DURATION"
}

flush_cache() {
  if [[ "$DRY_RUN" == "true" ]]; then
    return
  fi
  echo "  🧹 Flushing Redis cache..."
  redis-cli -h "$STAGING_HOST" -n 0 FLUSHDB || echo "  ⚠ Redis flush failed (continuing anyway)"
}

# === Pre-flight checks ===
echo "=== Pre-flight checks ==="
if [[ "$DRY_RUN" != "true" ]]; then
  echo "Checking k6..."
  if ! command -v k6 &>/dev/null; then
    echo "ERROR: k6 not installed. Run: brew install k6"
    exit 1
  fi
  echo "  ✓ k6 found"

  echo "Checking staging health..."
  HEALTH=$(curl -sf "${BASE_URL}/health" 2>/dev/null || echo '{"status":"unhealthy"}')
  echo "  Health: $HEALTH"
fi

mkdir -p "$RESULTS_DIR"

# === 1. Pre-seed data ===
run "Step 1: Pre-seed write entities" \
  k6 run "$LOADTEST_DIR/bulk_upsert.js" --vus 1 --iterations 2 \
    --env BASE_URL="$BASE_URL" --env API_KEY="$API_KEY" \
    --env SOURCE_BASE=loadtest-write-seed

# Extract UUIDs to write_entities.json
if [[ "$DRY_RUN" != "true" ]]; then
  echo "  Extracting entity UUIDs to write_entities.json..."
  psql -h "$STAGING_HOST" -U postgres -d dmo -t -A -F',' -c \
    "SELECT id::text, source, source_id FROM entities WHERE source = 'loadtest-write-seed' AND is_active = true;" \
    2>/dev/null | python3 -c "
import sys, json
rows = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    parts = line.split(',', 2)
    if len(parts) == 3:
        rows.append({'id': parts[0], 'source': parts[1], 'source_id': parts[2]})
with open('$LOADTEST_DIR/write_entities.json', 'w') as f:
    json.dump(rows, f, indent=2)
print(f'  ✓ Wrote {len(rows)} entities to write_entities.json')
" || echo "  ⚠ UUID extraction failed — write_mixed.js will use runtime pool only"
fi

rest

# === 2. Read-only ramp ===
run "Step 2: Read-only ramp (find breaking point)" \
  k6 run "$LOADTEST_DIR/search.js" \
    --env BASE_URL="$BASE_URL" \
    --stage 2m:50 --stage 3m:100 --stage 3m:200 --stage 3m:400 --stage 2m:800 --stage 2m:0 \
    --out json="$RESULTS_DIR/search-ramp.json"

rest

# === 4. Read-only sustained ===
run "Step 4: Read-only sustained (200 VUs, 5 min)" \
  k6 run "$LOADTEST_DIR/search.js" \
    --vus 200 --duration 5m \
    --env BASE_URL="$BASE_URL" \
    --out json="$RESULTS_DIR/search-sustained.json"

rest

# === 6. Read-only cold cache ===
flush_cache
run "Step 6: Read-only cold cache (50 VUs, 2 min)" \
  k6 run "$LOADTEST_DIR/search.js" \
    --vus 50 --duration 2m \
    --env BASE_URL="$BASE_URL" \
    --out json="$RESULTS_DIR/search-cold.json"

rest

# === 8. Stampede protection ===
flush_cache
run "Step 8: Stampede protection (100 VUs, same URL)" \
  k6 run "$LOADTEST_DIR/stampede.js" \
    --env BASE_URL="$BASE_URL" \
    --env TARGET_SOURCE=tourpedia \
    --env TARGET_ID=429403

rest

# === 10. Timeout saturation ===
flush_cache
run "Step 10: Timeout saturation (ramp to 100 VUs)" \
  k6 run "$LOADTEST_DIR/timeout_saturation.js" \
    --env BASE_URL="$BASE_URL" \
    --out json="$RESULTS_DIR/timeout-saturation.json"

rest

# === 12. Write-only bulk (single source) ===
run "Step 12: Bulk write — single source (5 VUs, 2 min)" \
  k6 run "$LOADTEST_DIR/bulk_upsert.js" \
    --vus 5 --duration 2m \
    --env BASE_URL="$BASE_URL" --env API_KEY="$API_KEY" \
    --env SOURCE_BASE=loadtest \
    --out json="$RESULTS_DIR/bulk-single-source.json"

rest

# === Write-only bulk (multi source) ===
run "Step (cont): Bulk write — multi source (8 VUs, 2 min)" \
  k6 run "$LOADTEST_DIR/bulk_upsert.js" \
    --vus 8 --duration 2m \
    --env BASE_URL="$BASE_URL" --env API_KEY="$API_KEY" \
    --env SOURCE_BASE=loadtest --env MULTI_SOURCE=true \
    --out json="$RESULTS_DIR/bulk-multi-source.json"

rest

# === 14. Individual write paths ===
run "Step 14: Individual write paths (2 VUs, 5 min)" \
  k6 run "$LOADTEST_DIR/write_mixed.js" \
    --vus 2 --duration 5m \
    --env BASE_URL="$BASE_URL" --env API_KEY="$API_KEY" \
    --env SOURCE=loadtest-write-seed \
    --out json="$RESULTS_DIR/write-mixed.json"

rest

# === 16. Mixed read+write ===
echo ""
echo "============================================================"
echo "  Step 16: Mixed read+write (10 min)"
echo "============================================================"
echo "  NOTE: This runs TWO k6 processes in parallel."
echo "  Read P95 will be worse than pure-read due to cache thrashing."
if [[ "$DRY_RUN" == "true" ]]; then
  echo "  [DRY RUN — skipping]"
else
  k6 run "$LOADTEST_DIR/bulk_upsert.js" \
    --vus 2 --duration 10m \
    --env BASE_URL="$BASE_URL" --env API_KEY="$API_KEY" \
    --env SOURCE_BASE=batch-import &
  BULK_PID=$!

  k6 run "$LOADTEST_DIR/search.js" \
    --vus 200 --duration 10m \
    --env BASE_URL="$BASE_URL" \
    --out json="$RESULTS_DIR/mixed-read-write.json" &
  READ_PID=$!

  wait $BULK_PID $READ_PID
  echo "  ✓ Both processes completed"
fi

rest

# === 18. Re-enable rate limiting ===
echo ""
echo "============================================================"
echo "  Step 18: Re-enable rate limiting"
echo "============================================================"
echo "  ⚠ REQUIRES MANUAL ACTION on staging VM:"
echo "    export RATE_LIMIT_ENABLED=true"
echo "    docker compose -f docker-compose.prod.yml restart api"
echo "  Then press Enter to continue..."
if [[ "$DRY_RUN" != "true" ]]; then
  read -r
fi

# === 19. Rate limit edge case ===
run "Step 19a: Rate limit — single IP (50 RPS, 2000 iterations)" \
  k6 run "$LOADTEST_DIR/search.js" \
    --vus 1 --iterations 2000 --rps 50 \
    --env BASE_URL="$BASE_URL"

run "Step 19b: Rate limit — multi IP (20 VUs, 2 min)" \
  k6 run "$LOADTEST_DIR/ratelimit_multiip.js" \
    --vus 20 --duration 2m \
    --env BASE_URL="$BASE_URL" \
    --out json="$RESULTS_DIR/search-ratelimit.json"

rest

# === 21. Re-disable rate limiting for spatial stress ===
echo ""
echo "============================================================"
echo "  Step 21: Re-disable rate limiting for spatial stress"
echo "============================================================"
echo "  ⚠ REQUIRES MANUAL ACTION on staging VM:"
echo "    export RATE_LIMIT_ENABLED=false"
echo "    docker compose -f docker-compose.prod.yml restart api"
echo "  Then press Enter to continue..."
if [[ "$DRY_RUN" != "true" ]]; then
  read -r
fi

# === Spatial stress ===
run "Step (cont): Spatial stress (3 min)" \
  k6 run "$LOADTEST_DIR/spatial_stress.js" \
    --vus 20 --duration 3m \
    --env BASE_URL="$BASE_URL" \
    --out json="$RESULTS_DIR/spatial-stress.json"

rest

# === 23. Soak test ===
run "Step 23: Soak test (50 VUs, 30 min)" \
  k6 run "$LOADTEST_DIR/search.js" \
    --vus 50 --duration 30m \
    --env BASE_URL="$BASE_URL" \
    --out json="$RESULTS_DIR/soak-50vu-30m.json"

# === 24. Cleanup ===
echo ""
echo "============================================================"
echo "  Step 24: Cleanup — removing test data"
echo "============================================================"
if [[ "$DRY_RUN" != "true" ]]; then
  echo "  Running cleanup SQL..."
  psql -h "$STAGING_HOST" -U postgres -d dmo -f "$LOADTEST_DIR/cleanup.sql" || echo "  ⚠ Cleanup SQL failed"
  echo "  Flushing Redis..."
  redis-cli -h "$STAGING_HOST" -n 0 FLUSHDB || echo "  ⚠ Redis flush failed"
  echo "  ✓ Cleanup complete"
fi

echo ""
echo "============================================================"
echo "  ALL SCENARIOS COMPLETE"
echo "============================================================"
echo "  Results: $RESULTS_DIR/"
echo "  Next: run 'python scripts/analyze_queries.py' on staging"
echo "  and review Prometheus metrics."
