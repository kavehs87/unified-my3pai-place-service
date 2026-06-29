import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  stages: [
    { duration: '30s', target: 50 },
    { duration: '1m', target: 100 },
    { duration: '30s', target: 0 },
  ],
};

export default function () {
  // Largest bbox + max page_size = heaviest spatial query.
  const res = http.get(`${BASE_URL}/map?bbox=5.9,45.8,10.5,47.8&page_size=100`);
  check(res, {
    'map 200 or 504': r => r.status === 200 || r.status === 504,
  });
  sleep(0.05);
}
