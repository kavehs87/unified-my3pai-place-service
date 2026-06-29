import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || 'test-key';
const SOURCE = __ENV.SOURCE || 'loadtest-write';

// Pre-seeded entity UUIDs loaded from write_entities.json (see §9.3).
// Each entry: { "id": "<uuid>", "source": "...", "source_id": "..." }
// The `id` is the internal UUID used for media/classification/entity_id lookups.
// `source`/`source_id` are used for PUT update requests (the update path is
// /{source}/{source_id}, NOT /{id}).
let entityPool;
try {
  entityPool = new SharedArray('writeEntities', function () {
    return JSON.parse(open('loadtest/write_entities.json'));
  });
} catch (_) {
  // Fallback: if no pre-seed file, the script will only exercise the create
  // branch (entityPool.length === 0 forces creates until populated at runtime).
  entityPool = [];
}

// Per-VU runtime pool of entities created during this test run.
// k6 VUs share SharedArray (read-only) but NOT mutable state across VUs,
// so each VU maintains its own createdEntities array. Entities created
// here are pushed in and used for subsequent update/media/classification.
const createdEntities = [];

// Weights: 50% entity create, 20% entity update, 15% media, 15% classification
const ENTITY_CREATE_WT = 0.50;
const ENTITY_UPDATE_WT = 0.20;
const MEDIA_WT = 0.15;
const CLASSIFICATION_WT = 0.15;

function pickEntity() {
  // Prefer runtime-created entities (we own their UUIDs); fall back to pre-seed pool.
  const pool = createdEntities.length > 0 ? createdEntities : entityPool;
  if (pool.length === 0) return null;
  return pool[Math.floor(Math.random() * pool.length)];
}

export default function () {
  const roll = Math.random();
  const iterSuffix = `${__VU}-${__ITER}`;

  if (roll < ENTITY_CREATE_WT || createdEntities.length === 0) {
    // Create a new single entity and capture the returned UUID for later use.
    const body = JSON.stringify({
      source: SOURCE,
      source_id: `single-${SOURCE}-${iterSuffix}`,
      name: `Write Test Entity ${iterSuffix}`,
      place_type: 'poi',
      latitude: 46.5 + Math.random(),
      longitude: 7.5 + Math.random() * 4,
      country: 'Switzerland',
      attributes: { test_run: true, type: 'single-create' },
    });
    const res = http.post(`${BASE_URL}/entities`, body, {
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    });
    const ok = check(res, { 'entity create 201': r => r.status === 201 });
    if (ok) {
      try {
        const body = res.json();
        // POST /entities returns the created entity including its `id` (UUID).
        // Push {id, source, source_id} so update/media/classification can use it.
        createdEntities.push({
          id: body.id,
          source: SOURCE,
          source_id: `single-${SOURCE}-${iterSuffix}`,
        });
      } catch (_) { /* response parse failure — skip */ }
    }

  } else if (roll < ENTITY_CREATE_WT + ENTITY_UPDATE_WT) {
    // PUT /{source}/{source_id} → 200 (NOT 201). Update path is keyed by
    // source+source_id, not by UUID.
    const ent = pickEntity();
    if (!ent) { sleep(0.1); return; }
    const body = JSON.stringify({
      name: `Updated Entity ${iterSuffix}`,
      attributes: { test_run: true, updated: Date.now() },
    });
    const res = http.put(`${BASE_URL}/${ent.source}/${ent.source_id}`, body, {
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    });
    check(res, { 'entity update 200': r => r.status === 200 });

  } else if (roll < ENTITY_CREATE_WT + ENTITY_UPDATE_WT + MEDIA_WT) {
    // POST /media → 201. Requires entity_id as a UUID.
    const ent = pickEntity();
    if (!ent) { sleep(0.1); return; }
    const body = JSON.stringify({
      entity_id: ent.id,  // UUID — NOT source/source_id
      media_type: 'image',
      url: `https://example.com/test-${iterSuffix}.jpg`,
      name: `Test Media ${iterSuffix}`,
      sort_order: 1,
    });
    const res = http.post(`${BASE_URL}/media`, body, {
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    });
    check(res, { 'media create 201': r => r.status === 201 });

  } else {
    // POST /classifications → 201. Requires entity_id (UUID), category, value_code.
    const ent = pickEntity();
    if (!ent) { sleep(0.1); return; }
    const body = JSON.stringify({
      entity_id: ent.id,  // UUID — NOT source/source_id
      category: 'test_category',
      value_code: `test_${iterSuffix}`,
      value_title: `Test Value ${iterSuffix}`,
    });
    const res = http.post(`${BASE_URL}/classifications`, body, {
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
    });
    check(res, { 'classification create 201': r => r.status === 201 });
  }

  sleep(0.1);
}
