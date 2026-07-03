#!/usr/bin/env python3
"""
CLI entry point for rephrase_from_source admin script.

Usage:
    uv run python scripts/rephrase.py --source rexby --dry-run
    uv run python scripts/rephrase.py --source rexby --limit 100
    uv run python scripts/rephrase.py --source rexby              # resume

    # Custom LLM (llama.cpp, Ollama, etc.):
    uv run python scripts/rephrase.py --source rexby --limit 20 --dry-run \\
        --llm-endpoint http://10.0.2.2:8080/v1 --llm-model local-qwen
"""

import argparse
import asyncio
import os
import signal
import sys
from datetime import UTC, datetime

from dmo.admin.llm_client import LLMClient
from dmo.admin.settings_manager import load_settings
from dmo.admin_scripts.rephrase_from_source import RephraseFromSource, DEFAULT_MAX_TOKENS


def ts():
    return datetime.now(UTC).strftime("%H:%M:%S")


def log(msg):
    print(f"  [{ts()}] {msg}")


def header(title):
    width = 60
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print(f"{'=' * width}")


async def main():
    parser = argparse.ArgumentParser(
        description="Rephrase entity data via LLM and create new entities under a target source"
    )
    parser.add_argument("--source", required=True, help="Source to read from (e.g. rexby)")
    parser.add_argument("--target-source", default="my3pai", help="Target source name")
    parser.add_argument("--prefix", default="rx:", help="Prefix for new source_id")
    parser.add_argument("--limit", type=int, default=0, help="Limit entities (0 = unlimited)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--batch-size", type=int, default=5, help="LLM batch size")
    parser.add_argument("--db-batch-size", type=int, default=50, help="DB commit batch size")
    parser.add_argument("--temperature", type=float, default=1.0, help="LLM temperature")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent LLM calls (3-5 recommended)")
    parser.add_argument("--entity-id", default="", help="Target specific entity UUID")
    parser.add_argument("--db-url", default=None, help="Database URL (overrides .env)")
    parser.add_argument("--llm-endpoint", default=None, help="LLM API endpoint URL (overrides admin settings)")
    parser.add_argument("--llm-model", default=None, help="LLM model name (overrides admin settings)")
    parser.add_argument("--llm-api-key", default=None, help="LLM API key (overrides admin settings)")

    args = parser.parse_args()

    header(f"Rephrase From Source — {'DRY RUN' if args.dry_run else 'LIVE RUN'}")
    log(f"Started: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    log(f"Source: {args.source}")
    log(f"Target: {args.target_source}")
    log(f"Prefix: {args.prefix}")
    log(f"Limit: {args.limit or 'unlimited'}")

    # Setup database
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from dmo.config import settings

    db_url = args.db_url or settings.database_url
    if not db_url:
        log("ERROR: No DATABASE_URL configured")
        sys.exit(1)

    log(f"DB: {db_url.split('@')[-1] if '@' in db_url else 'configured'}")

    engine = create_async_engine(
        db_url,
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

    # Setup LLM
    import httpx
    
    class GenericLLM:
        """OpenAI-compatible LLM client that handles both standard and reasoning models."""
        def __init__(self, api_key, base_url, model, max_tokens):
            self.api_key = api_key
            self.base_url = base_url.rstrip("/")
            self.model = model
            self.max_tokens = max_tokens
        
        async def chat(self, messages, temperature=1.0, max_tokens=None):
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    } if self.api_key else {
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens or self.max_tokens,
                    },
                )
                response.raise_for_status()
                data = response.json()
                msg = data["choices"][0]["message"]
                
                # Handle both standard and reasoning models:
                # - Standard (DeepSeek, GPT): content has the clean response JSON
                # - Reasoning (Qwen at llama.cpp via vLLM): content has the response JSON
                # - Reasoning (Qwen at llama.cpp via raw llama.cpp): reasoning_content has full output
                content = msg.get("content", "").strip()
                reasoning = msg.get("reasoning_content", "").strip()
                
                # Prefer content if it has clean JSON, otherwise fall back to reasoning_content
                if not content.startswith("{") and reasoning:
                    content = reasoning
                
                if not content:
                    raise ValueError("Empty content from LLM")
                
                h = hash(content[:80]) % 100000
                log(f"LLM response #{h} ({len(content)} chars)")
                return content
    
    # Priority: CLI args > admin settings > OpenCode Zen
    llm = None
    
    if args.llm_endpoint:
        llm = GenericLLM(
            api_key=args.llm_api_key or "",
            base_url=args.llm_endpoint,
            model=args.llm_model or "default",
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        log(f"Using CLI-specified LLM: {args.llm_endpoint} / {args.llm_model or 'default'}")
    else:
        admin_settings = await load_settings()
        llm = LLMClient.from_settings(admin_settings)
        if llm:
            log(f"Using admin-configured LLM: {llm.endpoint} / {llm.model}")
        else:
            from dmo.admin_scripts.rephrase_from_source import (
                OPENCODE_ZEN_API_KEY,
                OPENCODE_ZEN_BASE_URL,
                DEFAULT_MODEL,
            )
            log(f"Using OpenCode Zen LLM: {DEFAULT_MODEL}")
            llm = GenericLLM(
                api_key=OPENCODE_ZEN_API_KEY,
                base_url=OPENCODE_ZEN_BASE_URL,
                model=DEFAULT_MODEL,
                max_tokens=DEFAULT_MAX_TOKENS,
            )
    
    log(f"LLM ready")

    db = AsyncSession(engine)

    # Setup signal handler for graceful stop
    stop_event = asyncio.Event()

    def signal_handler():
        log("\nStop signal received. Creating .stop file...")
        with open(".stop", "w") as f:
            f.write("stop")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Build params
        params = {
            "source": args.source,
            "target_source": args.target_source,
            "prefix": args.prefix,
            "max_entities": args.limit,
            "dry_run": args.dry_run,
            "batch_size": args.batch_size,
            "db_batch_size": args.db_batch_size,
            "llm_temperature": str(args.temperature),
            "concurrency": args.concurrency,
            "entity_id": args.entity_id,
        }

        # Run script
        script = RephraseFromSource()
        result = await script.run(params, db, llm=llm)

        log(f"\nResult: {result.message}")
        if result.details:
            for d in result.details[:10]:
                log(f"  → {d}")

        if not result.success:
            sys.exit(1)

    except KeyboardInterrupt:
        log("\nInterrupted by user")
    except Exception as e:
        log(f"\nFATAL: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        await db.close()
        await engine.dispose()

    header("Complete!")


if __name__ == "__main__":
    asyncio.run(main())
