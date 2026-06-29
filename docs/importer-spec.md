# Importer Specification

This document is the single source of truth for importer authors. It describes how to map
your source data to the unified DMO schema.

## Required Fields

Every entity must include these fields:

| Field | Type | Description |
|---|---|---|
| `source` | string | Your source identifier (e.g., `"osm"`, `"tourpedia"`, `"rexby"`) |
| `source_id` | string | Unique ID from your source system |
| `name` | string | Display name for the entity |
| `place_type` | string | Your source's category value (e.g., `"restaurant"`, `"hotel"`, `"poi"`) |

The `place_type` value will be mapped to a unified category by the `unify_place_types` admin
script. Set `place_type` only; the system derives the unified category.

## Standard Field Mapping

Map your source fields to the schema columns below. All fields are optional unless marked
required.

| Your field → Schema column | Description |
|---|---|
| `url` → `website` | Canonical website URL |
| `image` → `thumbnail_url` | Primary photo/thumbnail URL |
| `lat` / `lng` → `latitude` / `longitude` | Geographic coordinates (both required or neither) |
| `iso_country` → `country` | ISO 3166-1 alpha-2 country code |
| `city` → `locality` | City, town, or area name |
| `state` → `region` | Primary region/state |
| `phone` → `phone` | Contact phone number |
| `email` → `email` | Contact email address |
| `address` → `address` | Full street address |
| `postal_code` → `postal_code` | ZIP/postal code |
| `description` → `description` | Raw description text |
| `description_format` → `description_format` | Format: `"text"`, `"prosemirror"`, `"markdown"` |
| `price_min` → `price_min` | Minimum price (numeric) |
| `price_max` → `price_max` | Maximum price (numeric) |
| `currency` → `currency` | ISO 4217 currency code |
| `price_level` → `price_level` | 0 (free) to 4 (very expensive) |
| `rating` → `rating` | 0.0 – 5.0 |
| `reviews_count` → `reviews_count` | Total number of reviews |
| `opening_hours` → `opening_hours` | Schedule JSON or plain text |
| `access_type` → `access_type` | `"FREE"`, `"PAID"`, `"FREE_PAID"`, `"UNKNOWN"` |
| `is_free` → `is_free` | Boolean |
| `is_barrier_free` → `is_barrier_free` | Boolean |
| `wheelchair_accessible` → `wheelchair_accessible` | Boolean |

## Classification Format

Bulk-insert classifications via `POST /classifications` after entity creation:

```json
{
  "entity_id": "uuid-of-your-entity",
  "category": "amenity|service|feature|tag|secondary_category",
  "value_code": "unique_code_for_this_value",
  "value_title": "Human-readable label"
}
```

| Category | Use For |
|---|---|
| `amenity` | OSM-style amenity tags (e.g., `"cuisine": "pizza"`) |
| `service` | Tourpedia-style services (e.g., `"wifi"`, `"parking"`) |
| `feature` | Tourpedia-style features (e.g., `"indoor"`, `"family_friendly"`) |
| `secondary_category` | Rexby-style secondary categories |
| `tag` | DZT-style free-form tags |

Classifications are upserted on `(entity_id, category, value_code)`. Duplicate inserts are
silently ignored.

## Attributes Rules

The `attributes` JSONB column is the overflow for anything that doesn't fit typed columns.

### Prefix convention
Prefix source-specific keys with your source name to avoid collisions:
- `"yoursource_fieldname"` — e.g., `"rexby_activity_level"`, `"tourpedia_services"`

### Route / Tour data
Store in `attributes`: `distance_km`, `duration_min`, `ascent_m`, `descent_m`,
`estimated_duration`, `is_self_guided`, `reservation_required`, `tourist_type`,
`season_text`, `itinerary`, `sub_trip`, `part_of_trip`

### Food & Beverage data
Store in `attributes`: `cuisine_type`, `serves_meal`, `serves_category`,
`accepts_reservations`, `dine_in`, `takeout`, `delivery`, `curbside_pickup`

### Amenity data
Store amenity flags in `attributes` too (no separate amenities column):
`dine_in`, `takeout`, `delivery`, `curbside_pickup`, `accepts_reservations`,
`cuisine_type`

## Derived Fields (DO NOT SET)

These fields are computed by the system or admin scripts. **Importers must NOT set them.**
If you send them via `EntityCreate`, they will be silently ignored (Pydantic drops unknown
fields).

| Field | Derived By |
|---|---|
| `unified_category` | `unify_place_types` admin script (from `place_type` mapping) |
| `unified_subcategory` | `unify_place_types` admin script (from `place_type` mapping) |
| `unified_category_id` | `unify_place_types` admin script (FK to `unified_categories`) |
| `quality_score` | `score_osm_entities` admin script |
| `enriched_at` | Enrichment admin scripts |

Importers set `place_type` only. The unification layer maps it to the unified category via
the `place_type_mappings` table.
