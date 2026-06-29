#!/usr/bin/env python3
"""
Phase 2: Data Unification — Admin Script Runner

Executes all admin scripts in order with a dedicated engine (isolated from API pool).
Each script runs dry-run first, then live run. Final verification queries confirm state.
"""
import asyncio
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, "/app/src")


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(msg):
    print(f"  [{ts()}] {msg}")


def header(title):
    width = 60
    print(f"\n{'='*width}")
    print(f"  {title}")
    print(f"{'='*width}")


def step_header(step, total, name):
    print(f"\n[Step {step}/{total}] {name}")
    print(f"{'─'*len(name)}")


async def run_script(script_cls, params, db, label):
    """Run a script instance with the given session. Returns ScriptResult."""
    instance = script_cls()
    result = await instance.run(params, db)
    return result


async def run_phase(dry_run, db):
    start = time.monotonic()

    header(f"Phase 2: Data Unification — {'DRY RUN' if dry_run else 'LIVE RUN'}")
    log(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    from dmo.admin_scripts.normalize_place_types import NormalizePlaceTypes
    from dmo.admin_scripts.unify_place_types import UnifyPlaceTypes
    from dmo.admin_scripts.extract_attributes import ExtractAttributes
    from dmo.admin_scripts.unify_classifications import UnifyClassifications
    from dmo.admin_scripts.clean_dzt_data import CleanDztData

    steps = [
        ("normalize_place_types", NormalizePlaceTypes, {"dry_run": dry_run, "batch_size": 500}),
        ("unify_place_types", UnifyPlaceTypes, {"dry_run": dry_run, "batch_size": 500}),
        ("extract_attributes", ExtractAttributes, {"dry_run": dry_run, "force": False, "batch_size": 500}),
        ("unify_classifications", UnifyClassifications, {"dry_run": dry_run, "batch_size": 500}),
        ("clean_dzt_data", CleanDztData, {"dry_run": dry_run, "batch_size": 1000}),
    ]

    all_results = {}
    for i, (name, cls, params) in enumerate(steps, 1):
        step_header(i, len(steps), name)
        t0 = time.monotonic()
        try:
            result = await run_script(cls, params, db, name)
            elapsed = time.monotonic() - t0
            log(f"Result: {result.message}")
            if result.details:
                for d in result.details[:10]:
                    log(f"  → {d}")
            all_results[name] = {"result": result, "elapsed": elapsed}
        except Exception as e:
            elapsed = time.monotonic() - t0
            log(f"FAILED after {elapsed:.1f}s: {e}")
            all_results[name] = {"error": str(e), "elapsed": elapsed}
            raise

    elapsed = time.monotonic() - start
    log(f"\nPhase complete in {elapsed:.1f}s")
    return all_results


async def verify(db):
    """Run verification queries against the DB."""
    from sqlalchemy import text

    header("VERIFICATION")

    total = (await db.execute(text("SELECT COUNT(*) FROM entities WHERE is_active = TRUE"))).scalar() or 0
    unified = (await db.execute(text("SELECT COUNT(*) FROM entities WHERE is_active = TRUE AND unified_category IS NOT NULL"))).scalar() or 0
    pct = (unified / total * 100) if total else 0
    log(f"Unified category coverage: {pct:.1f}% ({unified:,} / {total:,})")

    classif_total = (await db.execute(text("SELECT COUNT(*) FROM classifications WHERE is_active = TRUE"))).scalar() or 0
    log(f"Classifications total: {classif_total:,}")

    by_source = (await db.execute(text("""
        SELECT e.source, COUNT(c.id) as cnt
        FROM entities e
        LEFT JOIN classifications c ON c.entity_id = e.id AND c.is_active = TRUE
        WHERE e.is_active = TRUE
        GROUP BY e.source
        ORDER BY cnt DESC
    """))).fetchall()
    for source, cnt in by_source:
        log(f"  {source}: {cnt:,} classifications")

    website = (await db.execute(text("SELECT COUNT(*) FROM entities WHERE is_active = TRUE AND website IS NOT NULL"))).scalar() or 0
    thumb = (await db.execute(text("SELECT COUNT(*) FROM entities WHERE is_active = TRUE AND thumbnail_url IS NOT NULL"))).scalar() or 0
    log(f"Websites populated: {website:,} / {total:,} ({website/total*100:.1f}%)")
    log(f"Thumbnails populated: {thumb:,} / {total:,} ({thumb/total*100:.1f}%)")

    dzt_country = (await db.execute(text("SELECT COUNT(*) FROM entities WHERE source = 'dzt' AND is_active = TRUE AND country LIKE 'http%'"))).scalar() or 0
    dzt_region_null = (await db.execute(text("SELECT COUNT(*) FROM entities WHERE source = 'dzt' AND is_active = TRUE AND region IN ('n.v.', '??')"))).scalar() or 0
    log(f"DZT country URLs remaining: {dzt_country}")
    log(f"DZT region placeholders remaining: {dzt_region_null}")

    unmapped = (await db.execute(text("""
        SELECT e.place_type, e.source, COUNT(*) as cnt
        FROM entities e
        LEFT JOIN place_type_mappings m ON m.source = e.source AND m.source_place_type = e.place_type
        WHERE e.is_active = TRUE AND m.id IS NULL
        GROUP BY e.place_type, e.source
        ORDER BY cnt DESC
    """))).fetchall()
    if unmapped:
        log(f"Unmapped place_types ({len(unmapped)}):")
        for pt, src, cnt in unmapped:
            log(f"  {src}:{pt} = {cnt}")
    else:
        log("Unmapped place_types: 0")

    # Per-source unified coverage
    log("\nPer-source unified coverage:")
    coverage = (await db.execute(text("""
        SELECT e.source,
               COUNT(*) as total,
               COUNT(CASE WHEN e.unified_category IS NOT NULL THEN 1 END) as unified
        FROM entities e
        WHERE e.is_active = TRUE
        GROUP BY e.source
        ORDER BY e.source
    """))).fetchall()
    for source, total_s, unified_s in coverage:
        p = (unified_s / total_s * 100) if total_s else 0
        log(f"  {source}: {p:.1f}% ({unified_s:,} / {total_s:,})")


async def main():
    from dmo.config import settings
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

    header("Phase 2: Data Unification")
    db_url = settings.database_url or ""
    db_hint = db_url.split("@")[-1] if "@" in db_url else "configured"
    log(f"DB: {db_hint}")

    engine = create_async_engine(
        settings.database_url,
        echo=False,
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        isolation_level="READ_COMMITTED",
        connect_args={
            "server_settings": {"statement_timeout": "300000"},
            "prepared_statement_cache_size": 0,
        },
    )
    db = AsyncSession(engine)

    try:
        # Step 1: Dry run
        dry_results = await run_phase(dry_run=True, db=db)

        # Step 2: Check for errors before live run
        dry_errors = [name for name, data in dry_results.items() if "error" in data]
        if dry_errors:
            log(f"\nDry run had errors in: {', '.join(dry_errors)}")
            log("Aborting live run.")
            sys.exit(1)

        # Step 3: Live run
        live_results = await run_phase(dry_run=False, db=db)

        # Step 4: Verification
        await verify(db)

        # Summary
        header("SUMMARY")
        for name, data in live_results.items():
            if "error" in data:
                log(f"  {name}: FAILED — {data['error']}")
            else:
                elapsed = data["elapsed"]
                affected = data["result"].affected_count
                log(f"  {name}: {affected:,} affected ({elapsed:.1f}s)")

        header("Phase 2 complete!")
    except Exception as e:
        log(f"\nFATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        await db.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
