"""add unified taxonomy: unified_categories, place_type_mappings, entity unified columns, drop category_class

Revision ID: 011
Revises: 010
Create Date: 2026-06-26
"""

from alembic import op
from sqlalchemy import text

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | None = None
depends_on: str | None = None


# 9 top-level categories + ~45 subcategories (leaf nodes)
UNIFIED_CATEGORIES = [
    # Top-level (parent_id=None)
    ("food_drink", "Food & Drink", None, 1),
    ("accommodation", "Accommodation", None, 2),
    ("attraction", "Attractions", None, 3),
    ("culture", "Culture", None, 4),
    ("activity", "Activities", None, 5),
    ("transportation", "Transportation", None, 6),
    ("shopping", "Shopping", None, 7),
    ("nature", "Nature", None, 8),
    ("wellness", "Wellness", None, 9),
    # food_drink subcategories
    ("restaurant", "Restaurant", "food_drink", 1),
    ("cafe", "Cafe", "food_drink", 2),
    ("bar", "Bar", "food_drink", 3),
    ("brewery", "Brewery", "food_drink", 4),
    ("food_truck", "Food Truck", "food_drink", 5),
    # accommodation subcategories
    ("hotel", "Hotel", "accommodation", 1),
    ("hostel", "Hostel", "accommodation", 2),
    ("apartment", "Apartment", "accommodation", 3),
    ("campsite", "Campsite", "accommodation", 4),
    ("guesthouse", "Guesthouse", "accommodation", 5),
    # attraction subcategories
    ("monument", "Monument", "attraction", 1),
    ("museum", "Museum", "attraction", 2),
    ("landmark", "Landmark", "attraction", 3),
    ("viewpoint", "Viewpoint", "attraction", 4),
    ("park", "Park", "attraction", 5),
    ("zoo", "Zoo", "attraction", 6),
    ("attraction_leaf", "Attraction", "attraction", 7),
    ("region", "Region", "attraction", 8),
    # culture subcategories
    ("church", "Church", "culture", 1),
    ("gallery", "Gallery", "culture", 2),
    ("theater", "Theater", "culture", 3),
    ("historical_site", "Historical Site", "culture", 4),
    # activity subcategories
    ("hiking", "Hiking", "activity", 1),
    ("cycling", "Cycling", "activity", 2),
    ("skiing", "Skiing", "activity", 3),
    ("climbing", "Climbing", "activity", 4),
    ("swimming", "Swimming", "activity", 5),
    ("leisure", "Leisure", "activity", 6),
    ("sports", "Sports", "activity", 7),
    ("tour", "Tour", "activity", 8),
    ("event", "Event", "activity", 9),
    # transportation subcategories
    ("station", "Station", "transportation", 1),
    ("airport", "Airport", "transportation", 2),
    ("parking", "Parking", "transportation", 3),
    ("ferry", "Ferry", "transportation", 4),
    # shopping subcategories
    ("supermarket", "Supermarket", "shopping", 1),
    ("market", "Market", "shopping", 2),
    ("shop", "Shop", "shopping", 3),
    # nature subcategories
    ("lake", "Lake", "nature", 1),
    ("mountain", "Mountain", "nature", 2),
    ("forest", "Forest", "nature", 3),
    ("waterfall", "Waterfall", "nature", 4),
    ("nature_park", "Nature Park", "nature", 5),
    ("beach", "Beach", "nature", 6),
    ("library", "Library", "culture", 5),
    # wellness subcategories
    ("spa", "Spa", "wellness", 1),
    ("gym", "Gym", "wellness", 2),
    ("clinic", "Clinic", "wellness", 3),
]

# Mappings from source place_type to unified category slug (leaf)
PLACE_TYPE_MAPPINGS = [
    # ── OSM (33 existing + 40 new) ──
    ("osm", "apartment_rental", "apartment", 90),
    ("osm", "aquarium", "zoo", 80),
    ("osm", "art_center", "gallery", 80),
    ("osm", "art_gallery", "gallery", 100),
    ("osm", "attraction", "attraction_leaf", 50),
    ("osm", "airport", "airport", 100),
    ("osm", "bar", "bar", 100),
    ("osm", "bay", "beach", 70),
    ("osm", "beach", "beach", 80),
    ("osm", "bike_rental", "cycling", 70),
    ("osm", "bowling_alley", "leisure", 90),
    ("osm", "bus_station", "station", 90),
    ("osm", "cafe", "cafe", 100),
    ("osm", "camp_site", "campsite", 100),
    ("osm", "campground", "campsite", 90),
    ("osm", "car_rental", "station", 60),
    ("osm", "castle", "monument", 90),
    ("osm", "casino", "leisure", 80),
    ("osm", "cave", "monument", 70),
    ("osm", "church", "church", 100),
    ("osm", "clinic", "clinic", 100),
    ("osm", "cultural_center", "gallery", 80),
    ("osm", "fast_food", "restaurant", 100),
    ("osm", "fast_food_restaurant", "restaurant", 100),
    ("osm", "ferry_terminal", "ferry", 90),
    ("osm", "fitness_center", "gym", 100),
    ("osm", "garden", "nature_park", 80),
    ("osm", "gallery", "gallery", 100),
    ("osm", "guest_house", "guesthouse", 100),
    ("osm", "gym", "gym", 100),
    ("osm", "heritage_site", "historical_site", 90),
    ("osm", "hostel", "hostel", 100),
    ("osm", "hotel", "hotel", 100),
    ("osm", "ice_cream_shop", "cafe", 80),
    ("osm", "leisure", "leisure", 60),
    ("osm", "library", "library", 90),
    ("osm", "lighthouse", "viewpoint", 70),
    ("osm", "lodging", "hotel", 80),
    ("osm", "market", "market", 90),
    ("osm", "marketplace", "market", 100),
    ("osm", "memorial", "monument", 100),
    ("osm", "monument", "monument", 100),
    ("osm", "motel", "hotel", 100),
    ("osm", "mountain", "mountain", 80),
    ("osm", "movie_theater", "theater", 90),
    ("osm", "museum", "museum", 100),
    ("osm", "natural_feature", "mountain", 60),
    ("osm", "night_club", "bar", 80),
    ("osm", "palace", "monument", 90),
    ("osm", "park", "nature_park", 100),
    ("osm", "parking", "parking", 100),
    ("osm", "place_of_worship", "church", 80),
    ("osm", "poi", "attraction_leaf", 30),
    ("osm", "pub", "bar", 100),
    ("osm", "restaurant", "restaurant", 100),
    ("osm", "ruins", "monument", 90),
    ("osm", "rv_park", "campsite", 80),
    ("osm", "sauna", "spa", 90),
    ("osm", "scenic_overlook", "viewpoint", 100),
    ("osm", "scenic_viewpoint", "viewpoint", 100),
    ("osm", "shop", "shop", 100),
    ("osm", "ski_resort", "skiing", 90),
    ("osm", "spa", "spa", 100),
    ("osm", "sports", "sports", 80),
    ("osm", "sports_complex", "sports", 90),
    ("osm", "station", "station", 100),
    ("osm", "statue", "monument", 90),
    ("osm", "store", "shop", 90),
    ("osm", "supermarket", "supermarket", 100),
    ("osm", "theater", "theater", 90),
    ("osm", "theatre", "theater", 100),
    ("osm", "theme_park", "attraction_leaf", 80),
    ("osm", "tourism", "attraction_leaf", 40),
    ("osm", "tourist_attraction", "attraction_leaf", 80),
    ("osm", "tower", "monument", 80),
    ("osm", "transit_station", "station", 90),
    ("osm", "vacation_rental", "apartment", 90),
    ("osm", "valley", "mountain", 60),
    ("osm", "viewpoint", "viewpoint", 100),
    ("osm", "water_park", "leisure", 80),
    ("osm", "wellness_center", "spa", 90),
    ("osm", "wildlife_refuge", "nature_park", 80),
    ("osm", "zoo", "zoo", 100),
    # ── Tourpedia ──
    ("tourpedia", "accommodation", "hotel", 90),
    ("tourpedia", "attraction", "attraction_leaf", 60),
    ("tourpedia", "poi", "attraction_leaf", 30),
    ("tourpedia", "restaurant", "restaurant", 100),
    # ── Rexby ──
    ("rexby", "activity", "leisure", 60),
    ("rexby", "attraction", "attraction_leaf", 60),
    ("rexby", "cafe", "cafe", 100),
    ("rexby", "experience", "attraction_leaf", 60),
    ("rexby", "foodanddrink", "restaurant", 90),
    ("rexby", "guide", "tour", 80),
    ("rexby", "hotel", "hotel", 100),
    ("rexby", "museum", "museum", 100),
    ("rexby", "restaurant", "restaurant", 100),
    ("rexby", "stay", "hotel", 90),
    ("rexby", "transportation", "station", 70),
    ("rexby", "unknown", "attraction_leaf", 30),
    # ── Swiss DMO ──
    ("swiss_dmo", "accommodation", "hotel", 90),
    ("swiss_dmo", "attraction", "attraction_leaf", 60),
    ("swiss_dmo", "destination", "region", 70),
    ("swiss_dmo", "restaurant", "restaurant", 100),
    ("swiss_dmo", "tour", "tour", 80),
    # ── DZT ──
    ("dzt", "church", "church", 100),
    ("dzt", "event", "event", 70),
    ("dzt", "food_establishment", "restaurant", 90),
    ("dzt", "library", "library", 80),
    ("dzt", "museum", "museum", 100),
    ("dzt", "park", "nature_park", 90),
    ("dzt", "place_of_worship", "church", 80),
    ("dzt", "poi", "attraction_leaf", 50),
    ("dzt", "point_of_interest", "attraction_leaf", 50),
    ("dzt", "region", "region", 40),
    ("dzt", "tourist_attraction", "attraction_leaf", 80),
    ("dzt", "trail", "hiking", 80),
]


def upgrade() -> None:
    # ── Step 1: Create unified_categories table ──
    op.execute("""
        CREATE TABLE unified_categories (
            id              SERIAL PRIMARY KEY,
            slug            VARCHAR(100) NOT NULL UNIQUE,
            name            VARCHAR(200) NOT NULL,
            parent_id       INTEGER REFERENCES unified_categories(id) ON DELETE SET NULL,
            icon            VARCHAR(50),
            sort_order      INTEGER DEFAULT 0,
            is_active       BOOLEAN DEFAULT TRUE,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # ── Step 2: Create place_type_mappings table ──
    op.execute("""
        CREATE TABLE place_type_mappings (
            id                  SERIAL PRIMARY KEY,
            source              VARCHAR(100) NOT NULL,
            source_place_type   VARCHAR(100) NOT NULL,
            unified_category_id INTEGER REFERENCES unified_categories(id) ON DELETE SET NULL,
            confidence          SMALLINT DEFAULT 100,
            is_manual           BOOLEAN DEFAULT FALSE,
            notes               TEXT,
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            updated_at          TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_source_place_type UNIQUE (source, source_place_type)
        )
    """)
    op.execute("CREATE INDEX idx_ptm_category ON place_type_mappings (unified_category_id)")
    op.execute("CREATE INDEX idx_ptm_source ON place_type_mappings (source)")

    # ── Step 3: Add unified columns to entities ──
    op.execute("ALTER TABLE entities ADD COLUMN unified_category_id INTEGER REFERENCES unified_categories(id)")
    op.execute("ALTER TABLE entities ADD COLUMN unified_category VARCHAR(100)")
    op.execute("ALTER TABLE entities ADD COLUMN unified_subcategory VARCHAR(100)")

    # ── Step 4: Create indexes on entities ──
    op.execute("CREATE INDEX idx_entity_unified_category ON entities (unified_category, is_active)")
    op.execute("CREATE INDEX idx_entity_unified_category_id ON entities (unified_category_id)")

    # ── Step 5: Migrate category_class → unified_category ──
    op.execute("""
        UPDATE entities SET unified_category = 'food_drink' WHERE category_class = 'food_and_drink'
    """)
    op.execute("""
        UPDATE entities SET unified_category = 'accommodation' WHERE category_class = 'stay'
    """)
    op.execute("""
        UPDATE entities SET unified_category = 'transportation' WHERE category_class = 'transportation'
    """)
    op.execute("""
        UPDATE entities SET unified_category = 'attraction' WHERE category_class = 'experience'
    """)

    # ── Step 6: Drop category_class ──
    op.execute("ALTER TABLE entities DROP COLUMN category_class")

    # ── Step 7: Seed unified_categories ──
    conn = op.get_bind()
    # Insert top-level first (no parent_id), then subcategories (parent_id=slug)
    top_level = [(s, n, so) for s, n, p, so in UNIFIED_CATEGORIES if p is None]
    sub_level = [(s, n, p, so) for s, n, p, so in UNIFIED_CATEGORIES if p is not None]

    for slug, name, sort_order in top_level:
        conn.execute(
            text(
                "INSERT INTO unified_categories (slug, name, sort_order) VALUES (:slug, :name, :sort)"
            ),
            {"slug": slug, "name": name, "sort": sort_order},
        )

    # Build slug→id map for parent lookup
    parent_ids = conn.execute(
        text("SELECT slug, id FROM unified_categories WHERE parent_id IS NULL")
    ).fetchall()
    slug_to_id = {row[0]: row[1] for row in parent_ids}

    for slug, name, parent_slug, sort_order in sub_level:
        parent_id = slug_to_id.get(parent_slug)
        conn.execute(
            text(
                "INSERT INTO unified_categories (slug, name, parent_id, sort_order) VALUES (:slug, :name, :parent, :sort)"
            ),
            {"slug": slug, "name": name, "parent": parent_id, "sort": sort_order},
        )

    # ── Step 8: Seed place_type_mappings ──
    leaf_ids = conn.execute(
        text("SELECT slug, id FROM unified_categories WHERE parent_id IS NOT NULL")
    ).fetchall()
    leaf_slug_to_id = {row[0]: row[1] for row in leaf_ids}

    for source, source_place_type, target_slug, confidence in PLACE_TYPE_MAPPINGS:
        category_id = leaf_slug_to_id.get(target_slug)
        if category_id is not None:
            conn.execute(
text(
                    "INSERT INTO place_type_mappings (source, source_place_type, unified_category_id, confidence) VALUES (:src, :spt, :cid, :conf)"
                ),
                {"src": source, "spt": source_place_type, "cid": category_id, "conf": confidence},
            )


def downgrade() -> None:
    # Reverse seed data deletion
    op.execute("DELETE FROM place_type_mappings")
    op.execute("DELETE FROM unified_categories")

    # Restore category_class
    op.execute("ALTER TABLE entities ADD COLUMN category_class VARCHAR(50)")

    # Drop unified columns
    op.execute("ALTER TABLE entities DROP COLUMN unified_subcategory")
    op.execute("ALTER TABLE entities DROP COLUMN unified_category")
    op.execute("ALTER TABLE entities DROP COLUMN unified_category_id")

    # Drop tables
    op.drop_table("place_type_mappings")
    op.drop_table("unified_categories")
