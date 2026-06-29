import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // Per-VU fake client IP. 20 VUs → 20 independent rate-limit windows.
  const fakeIp = `10.99.${Math.floor(__VU / 256)}.${__VU % 256}`;
  const params = {
    headers: { 'X-Forwarded-For': fakeIp },
  };
  const res = http.get(`${BASE_URL}/search?q=Zermatt&page_size=20`, params);
  check(res, {
    'status 200 or 429': r => r.status === 200 || r.status === 429,
  });
  sleep(0.05);
}
