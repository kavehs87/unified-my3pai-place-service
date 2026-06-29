import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

const denseBboxes = [
  '5.9,45.8,10.5,47.8',    // all Switzerland (verify entity count against staging DB)
  '8.53,47.35,8.56,47.37',  // Zurich centre (~7 km²)
  '6.0,46.2,7.2,46.5',      // Geneva/Lausanne (~100 km²)
];

export default function () {
  for (const bbox of denseBboxes) {
    const res = http.get(`${BASE_URL}/map?bbox=${bbox}&page_size=100`);
    check(res, { [`map ${bbox}`]: r => r.status === 200 });
  }
  sleep(0.1);
}
