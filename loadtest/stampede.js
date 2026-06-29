import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// Single shared target — all VUs hit this exact URL.
// Use a known entity from known_entities.json (§9.1).
const TARGET_SOURCE = __ENV.TARGET_SOURCE || 'osm';
const TARGET_ID = __ENV.TARGET_ID || '26554597';

export const options = {
  scenarios: {
    stampede: {
      executor: 'per-vu-iterations',
      vus: 100,
      iterations: 1,
      maxDuration: '30s',
    },
  },
};

export default function () {
  const res = http.get(`${BASE_URL}/${TARGET_SOURCE}/${TARGET_ID}`);
  check(res, {
    'stampede 200': r => r.status === 200,
  });
}
