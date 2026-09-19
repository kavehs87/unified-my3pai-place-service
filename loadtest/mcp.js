// MCP endpoint load test — read-only tools over Streamable HTTP (stateless JSON).
//
// Usage:
//   k6 run loadtest/mcp.js                              # default ramp 10 -> 200 VUs
//   k6 run -e BASE_URL=http://127.0.0.1:8010 loadtest/mcp.js
//   k6 run -e STAGES='[{"duration":"15s","target":50}]' loadtest/mcp.js
//
// Notes:
// - Stateless JSON mode: no session id, no initialize handshake required; each
//   iteration is one independent JSON-RPC tools/call POST.
// - With the default rate limit (1000 req/min/IP) a single-IP run will cap at
//   ~16.7 req/s and return 429s — that is the expected project base cap.
//   Disable rate limiting (RATE_LIMIT_ENABLED=false) to measure raw capacity.
//
// Measured baseline (2026-09-19, local dev DB 1.95M entities, 4 uvicorn workers,
// k6 on the same 10-core host — conservative):
//   100 VUs: 512 req/s, p50 110ms, p95 275ms, 0 errors
//   200 VUs: 975 req/s, p50 123ms, p95 253ms, 0 errors
//   400 VUs: 982 req/s, p50 298ms, p95 691ms, 0 errors (throughput plateau)
//   600 VUs: 927 req/s, p50 515ms, p95 1.2s,  0 errors (latency-bound)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8010';
const MCP_URL = `${BASE_URL}/mcp`;

const STAGES = __ENV.STAGES
  ? JSON.parse(__ENV.STAGES)
  : [
      { duration: '15s', target: 10 },
      { duration: '15s', target: 25 },
      { duration: '15s', target: 50 },
      { duration: '15s', target: 100 },
      { duration: '15s', target: 200 },
      { duration: '10s', target: 0 },
    ];

export const options = {
  scenarios: {
    mcp_read: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: STAGES,
      gracefulRampDown: '5s',
    },
  },
  thresholds: {
    // 429s are the rate-limit cap (expected when limiting is on); do not abort.
    http_req_failed: [{ threshold: 'rate<1', abortOnFail: false }],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const toolErrors = new Counter('mcp_tool_errors');
const rateLimited = new Counter('mcp_rate_limited');
const toolDuration = new Trend('mcp_tool_duration', true);

const HOT_QUERIES = ['Zermatt', 'Interlaken', 'Lucerne', 'Zurich', 'Bern', 'Geneva'];
const DETAIL_ENTITIES = [
  { source: 'osm', source_id: '567132773' }, // Fred Hotel, Zurich
  { source: 'osm', source_id: '3813747453' },
  { source: 'osm', source_id: '255288522' },
];

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

function callTool(name, args) {
  const body = JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'tools/call',
    params: { name, arguments: args },
  });
  const res = http.post(MCP_URL, body, {
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    tags: { tool: name },
  });

  if (res.status === 429) {
    rateLimited.add(1);
    return res;
  }

  toolDuration.add(res.timings.duration);

  let ok = res.status === 200;
  if (ok) {
    try {
      const payload = res.json();
      ok = payload.result && payload.result.isError === false;
    } catch (_) {
      ok = false;
    }
  }
  if (!ok) {
    toolErrors.add(1);
  }
  check(res, { 'tool call ok': () => ok });
  return res;
}

export default function () {
  const roll = Math.random();

  if (roll < 0.6) {
    callTool('search_places', { q: randomItem(HOT_QUERIES), page_size: 10 });
  } else if (roll < 0.8) {
    callTool('find_nearby', { lat: 47.3769, lon: 8.5417, radius_km: 10, page_size: 10 });
  } else if (roll < 0.9) {
    callTool('map_bounding_box', {
      min_lon: 8.5,
      min_lat: 47.3,
      max_lon: 8.6,
      max_lat: 47.4,
      page_size: 10,
    });
  } else {
    const entity = randomItem(DETAIL_ENTITIES);
    callTool('get_place', entity);
  }

  sleep(0.05);
}
