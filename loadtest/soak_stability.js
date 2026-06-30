import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

let knownEntities;
try {
  knownEntities = new SharedArray('entities', function () {
    return JSON.parse(open('known_entities.json'));
  });
} catch (_) {
  knownEntities = [
    { source: 'rexby', id: 'HHiVZoVqRGyE08Gt8kwGEA' },
    { source: 'tourpedia', id: '236446' },
  ];
}

export const options = {
  stages: [
    { duration: '1m', target: 10 },
    { duration: '28m', target: 10 },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<10000'],
  },
};

const base = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  const entity = knownEntities[Math.floor(Math.random() * knownEntities.length)];

  const resSearch = http.get(`${base}/search?q=Interlaken&page_size=20`);
  check(resSearch, { 'search 200': (r) => r.status === 200 });

  const resDetail = http.get(`${base}/${entity.source}/${entity.id}`);
  check(resDetail, { 'detail 200': (r) => r.status === 200 });

  const resNearby = http.get(`${base}/nearby?lat=46.68&lon=7.86&radius_km=10&page_size=20`);
  check(resNearby, { 'nearby 200': (r) => r.status === 200 });

  sleep(0.5);
}
