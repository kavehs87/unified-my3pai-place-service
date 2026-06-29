import http from 'k6/http';
import { check, sleep } from 'k6';
import { SharedArray } from 'k6/data';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// Realistic query pool — mix of hot (popular) and cold (long-tail) searches.
// For a true 80/15/5 hot/mid/cold distribution per §9.2, use weightedPick()
// (defined below) instead of randomItem(). randomItem() is uniform — fine
// for a first pass, but weighted sampling better reflects real traffic.
const searchTerms = ['Zermatt', 'Interlaken', 'Lucerne', 'Zurich', 'Bern', 'Geneva', 'Grindelwald', 'Lauterbrunnen', 'St. Moritz', 'Davos', 'Montreux', 'Lugano', 'Basel', 'Lausanne', 'Fribourg'];
const placeTypes = ['poi', 'hotel', 'restaurant', 'museum', 'park', ''];  // '' = all types (read-only; empty source would 422 on writes)
// NOTE: '' in sources is valid ONLY as a read query param (treated as no filter).
// Do NOT reuse this array for write payloads — source has min_length=1 (schemas.py:161).
const sources = ['osm', 'rexby', 'tourpedia', 'swiss_dmo', 'dzt', ''];
const bboxes = [
  '5.9,45.8,10.5,47.8',   // Switzerland
  '7.0,46.0,8.5,47.5',    // Bernese Oberland
  '8.5,47.3,8.6,47.4',    // Zurich centre
  '6.0,46.2,7.2,46.5',    // Geneva/Lausanne
];

// Load known entities from JSON (details in §9.1).
// IMPORTANT: let must be at block scope outside try/catch.
let knownEntities;
try {
  knownEntities = new SharedArray('entities', function () {
    return JSON.parse(open('loadtest/known_entities.json'));
  });
} catch (_) {
  knownEntities = [
    { source: 'osm', id: '26554597' },
    { source: 'osm', id: '1682267' },
    { source: 'osm', id: '26174239' },
    { source: 'osm', id: '26554551' },
  ];
}

function randomItem(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}

// Weighted pick — use this instead of randomItem(searchTerms) if you want
// the 80/15/5 hot/mid/cold split described in §9.2.
// const hotTerms = ['Zermatt','Interlaken','Zurich','Lucerne','Geneva'];
// const midTerms = ['Grindelwald','Lauterbrunnen','St. Moritz','Davos','Montreux','Lugano','Basel','Lausanne','Fribourg','Bern'];
// const coldTerms = ['xyznotatown','Zermat','Interlukin','','Basel Land'];
// function weightedPick() {
//   const r = Math.random();
//   if (r < 0.80) return randomItem(hotTerms);
//   if (r < 0.95) return randomItem(midTerms);
//   return randomItem(coldTerms);
// }

const SEARCH_WEIGHT = 0.35;
const NEARBY_WEIGHT = 0.25;
const MAP_WEIGHT = 0.10;
const DETAIL_WEIGHT = 0.12;
const CLASSIFICATIONS_WEIGHT = 0.05;
const CLASSIFICATIONS_LIST_WEIGHT = 0.05;
const UNIFIED_CATEGORIES_WEIGHT = 0.08;

// Sample classification categories for /classifications list
const classificationCategories = ['star_rating', 'cuisine_type', 'accessibility'];

export default function () {
  const roll = Math.random();

  if (roll < SEARCH_WEIGHT) {
    const q = randomItem(searchTerms);
    const place_type = randomItem(placeTypes);
    const source = randomItem(sources);
    let url = `${BASE_URL}/search?q=${encodeURIComponent(q)}&page_size=20`;
    if (place_type) url += `&place_type=${place_type}`;
    if (source) url += `&source=${source}`;
    const res = http.get(url);
    check(res, { 'search 200': r => r.status === 200 });

  } else if (roll < SEARCH_WEIGHT + NEARBY_WEIGHT) {
    // Random points within Switzerland
    const lat = 46.5 + (Math.random() - 0.5) * 2;
    const lon = 7.5 + (Math.random() - 0.5) * 4;
    // radius_km is capped at 500 (router.py:131). Values 1-50 are realistic.
    const radius = randomItem([1, 5, 10, 25, 50]);
    const res = http.get(`${BASE_URL}/nearby?lat=${lat}&lon=${lon}&radius_km=${radius}&page_size=20`);
    check(res, { 'nearby 200': r => r.status === 200 });

  } else if (roll < SEARCH_WEIGHT + NEARBY_WEIGHT + MAP_WEIGHT) {
    const bbox = randomItem(bboxes);
    const res = http.get(`${BASE_URL}/map?bbox=${bbox}&page_size=50`);
    check(res, { 'map 200': r => r.status === 200 });

  } else if (roll < SEARCH_WEIGHT + NEARBY_WEIGHT + MAP_WEIGHT + DETAIL_WEIGHT) {
    // Detail entities read from known_entities.json (or hardcoded fallback)
    const { source, id } = randomItem(knownEntities);
    const res = http.get(`${BASE_URL}/${source}/${id}`);
    check(res, { 'detail 200': r => r.status === 200 });

  } else if (roll < SEARCH_WEIGHT + NEARBY_WEIGHT + MAP_WEIGHT + DETAIL_WEIGHT + CLASSIFICATIONS_WEIGHT) {
    const res = http.get(`${BASE_URL}/classifications/categories`);
    check(res, { 'categories 200': r => r.status === 200 });

  } else if (roll < SEARCH_WEIGHT + NEARBY_WEIGHT + MAP_WEIGHT + DETAIL_WEIGHT + CLASSIFICATIONS_WEIGHT + CLASSIFICATIONS_LIST_WEIGHT) {
    // Random classification list queries
    const cat = randomItem(classificationCategories);
    const res = http.get(`${BASE_URL}/classifications?category=${cat}&page_size=20`);
    check(res, { 'classifications 200': r => r.status === 200 });

  } else {
    const res = http.get(`${BASE_URL}/unified-categories`);
    check(res, { 'unified-categories 200': r => r.status === 200 });
  }

  sleep(Math.random() * 0.2);  // 0-200ms think time
}
