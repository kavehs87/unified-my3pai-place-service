#!/usr/bin/env python3
"""Analyze slow queries from PostgreSQL pg_stat_statements.

Run with: python scripts/analyze_queries.py
Requires: pg_stat_statements extension enabled in PostgreSQL.
"""

import asyncio
import sys

from sqlmodel import text
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dmo.config import settings


async def main():
    engine = create_async_engine(settings.database_url_sync.replace("+asyncpg", "").replace("+psycopg2", ""))
    session = async_sessionmaker(engine, class_=AsyncSession)

    async with session() as s:
        # Top 20 queries by total time
        result = await s.exec(
            text("""
                SELECT queryid,
                       query,
                       calls,
                       total_exec_time::numeric / 1000 AS total_sec,
                       mean_exec_time::numeric AS mean_ms,
                       rows,
                       shared_blks_hit,
                       shared_blks_read
                FROM pg_stat_statements
                ORDER BY total_exec_time DESC
                LIMIT 20;
            """)
        )
        rows = result.all()

        if not rows:
            print("pg_stat_statements not enabled or empty. Enable with:")
            print("  CREATE EXTENSION IF NOT EXISTS pg_stat_statements;")
            sys.exit(0)

        print(f"{'Query ID':<10} {'Calls':<8} {'Total (s)':<12} {'Mean (ms)':<12} {'Rows':<10} {'Query'}")
        print("-" * 120)
        for row in rows:
            query_preview = (row.query or "?")[:80].replace("\n", " ")
            print(f"{row.queryid:<10} {row.calls:<8} {row.total_sec:<12.2f} {row.mean_ms:<12.2f} {row.rows:<10} {query_preview}")

        # Index usage stats
        result2 = await s.exec(
            text("""
                SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
                FROM pg_stat_user_indexes
                ORDER BY idx_scan DESC;
            """)
        )
        rows2 = result2.all()

        print(f"\n{'Schema':<15} {'Table':<20} {'Index':<30} {'Scans':<12} {'Tuples Read':<15}")
        print("-" * 100)
        for row in rows2:
            print(f"{row.schemaname:<15} {row.tablename:<20} {row.indexname:<30} {row.idx_scan:<12} {row.idx_tup_read:<15}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
