import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';

// SOURCE_BASE lets you set a shared prefix (e.g. 'loadtest' for single-source,
// or 'loadtest' with each VU appending __VU for multi-source test).
// For multi-source: set --env MULTI_SOURCE=true and the script suffixes __VU.
// IMPORTANT: Use a 'loadtest' prefix for ALL test data so cleanup (§9.4)
// can DELETE ... WHERE source LIKE 'loadtest%' without touching real data.
const SOURCE_BASE = __ENV.SOURCE_BASE || 'loadtest';
const MULTI_SOURCE = __ENV.MULTI_SOURCE === 'true';

export default function () {
  const source = MULTI_SOURCE ? `${SOURCE_BASE}-vu${__VU}` : SOURCE_BASE;
  const batchSize = 100;
  const entities = Array.from({ length: batchSize }, (_, i) => ({
    source: source,
    source_id: `${source}-${__ITER}-${i}`,  // __ITER avoids millisecond collisions
    name: `Load Test Entity ${__VU}-${__ITER}-${i}`,
    place_type: 'poi',
    latitude: 46.5 + Math.random(),
    longitude: 7.5 + Math.random() * 4,
    country: 'Switzerland',
    attributes: { test_run: true, timestamp: Date.now() },
  }));

  const res = http.post(`${BASE_URL}/entities/bulk`, JSON.stringify(entities), {
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY,
    },
  });

  check(res, { 'bulk 201': r => r.status === 201 });
  sleep(0.5);
}
