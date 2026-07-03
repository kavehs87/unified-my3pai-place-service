# Rephrase From Source — Feature Implementation

**Date:** 2026-07-03  
**Status:** ✅ Complete  
**PR:** Pending

---

## Overview

Implemented an admin script (`rephrase_from_source`) that rephrases entity data (name, summary, description) via LLM and creates new entities under a target source (e.g., `rexby` → `my3pai`). Original records remain untouched.

---

## Problem Statement

Tourism data from external sources (Rexby, OSM, Tourpedia, etc.) often has:
- Generic or low-quality descriptions ("Charming historic town", "Serene escape")
- Inconsistent formatting (HTML vs plain text)
- Missing or thin summaries
- Provider-specific language/style

**Goal:** Create a unified, high-quality content layer (`my3pai` source) with rephrased, engaging descriptions while preserving factual accuracy.

---

## Solution Architecture

### Components

1. **`src/dmo/admin_scripts/rephrase_from_source.py`** — Admin script (495 lines)
2. **`scripts/rephrase.py`** — CLI entry point for standalone execution
3. **`migrations/versions/012_add_rephrased_state.py`** — State tracking table
4. **`src/dmo/models/database.py`** — `My3paiRephrased` SQLModel
5. **`src/dmo/admin/router.py`** — Stop endpoint for graceful shutdown
6. **`tests/test_rephrase_from_source.py`** — 13 comprehensive tests

### Workflow

```
Rexby Entities (source=rexby)
    ↓
Read via keyset pagination (source_id ORDER BY)
    ↓
LLM Rephrasing (single API call per entity)
    ↓
Parse JSON response: {rephrased_name, rephrased_summary, rephrased_description}
    ↓
Generate slug from rephrased name (regex)
    ↓
Create new entity (source=my3pai, source_id=rx:<original>)
    ↓
Record in my3pai_rephrased state table
    ↓
Commit batch (default 50 entities)
```

---

## LLM Integration

### Prompt Design

**System Prompt:**
```
You are a professional tourism content writer. Rephrase the following 
tourism entity data. Make it sound fresh and original — not like a copy 
of the source — while preserving the factual meaning and intent.

Rules:
- Name: catchy but accurate (max 100 chars)
- Summary: 1-2 sentences capturing the essence
- Description: 2-4 paragraphs, engaging and informative. 
  Strip any HTML tags. Write in plain text.
- If any field is missing/empty, omit it from the output

Return ONLY valid JSON, no markdown, no explanation:
{
  "rephrased_name": "...",
  "rephrased_summary": "...",
  "rephrased_description": "..."
}
```

**User Prompt:**
```
Entity data:
Name: {name}
Summary: {summary}
Description: {description}
```

### LLM Configuration

- **Endpoint:** `http://10.0.2.2:8080` (Ollama/llamacpp)
- **Model:** `local-qwen` (Qwen2.5-72B-Q8.gguf)
- **Max Tokens:** 10240 (reasoning models need room for thought + output)
- **Temperature:** 0.8–1.0 (higher for more creative rephrasing)
- **Avg Response Time:** ~5-10s per entity

### Why 10240 Tokens?

The Qwen reasoning models consume tokens for `reasoning_content` before generating actual output. Testing showed:
- `max_tokens=100`: Only reasoning, no content
- `max_tokens=1000`: Reasoning + brief content
- `max_tokens=4096`: Full content generated
- `max_tokens=10240`: Comfortable margin for detailed descriptions

---

## Resume / Stop Mechanism

### State Tracking

**Table:** `my3pai_rephrased`
```sql
CREATE TABLE my3pai_rephrased (
    id           BIGSERIAL PRIMARY KEY,
    source       VARCHAR(100) NOT NULL,
    source_id    VARCHAR(500) NOT NULL,
    entity_id    UUID NOT NULL,
    rephrased_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source, source_id)
);
```

### Resume Logic

1. On startup, query all `source_id` values from `my3pai_rephrased` where `source = 'my3pai'`
2. Load into `seen_source_ids` set
3. When fetching entities, compute `new_source_id = prefix + orig_source_id`
4. Skip if `new_source_id in seen_source_ids`
5. Add to set after processing

### Stop Mechanism

- **File-based:** `.stop` file in CWD
- **Admin UI:** `POST /admin/scripts/{name}/stop` creates the file
- **CLI:** Ctrl+C triggers signal handler, writes `.stop` file
- **Behavior:** Checks between batches, commits current batch, returns partial result

---

## CLI Usage

```bash
# Dry run (preview)
uv run python scripts/rephrase.py --source rexby --dry-run

# Live run, 100 entities
uv run python scripts/rephrase.py --source rexby --limit 100

# Resume from where left off
uv run python scripts/rephrase.py --source rexby

# Specific entity for testing
uv run python scripts/rephrase.py --source rexby --entity-id <uuid>

# Custom target source and prefix
uv run python scripts/rephrase.py --source rexby --target-source my3pai --prefix rx:

# Custom LLM parameters
uv run python scripts/rephrase.py --source rexby --temperature 0.9 --max-tokens 8192
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `--source` | str | (required) | Source to read from (e.g., `rexby`) |
| `--target-source` | str | `my3pai` | Target source name for new entities |
| `--prefix` | str | `rx:` | Prefix for new source_id |
| `--limit` | int | `0` | Limit entities (0 = unlimited) |
| `--dry-run` | flag | — | Preview without writing |
| `--batch-size` | int | `5` | LLM batch size (rate limiting) |
| `--db-batch-size` | int | `50` | DB commit batch size |
| `--temperature` | float | `1.0` | LLM temperature |
| `--entity-id` | str | `""` | Target specific entity UUID |

---

## Admin UI Integration

The script auto-discovers via `registry.py` and appears in the Scripts page.

### Running from UI

1. Navigate to **Tools → Scripts**
2. Find `rephrase_from_source`
3. Configure parameters:
   - **Source:** `rexby`
   - **Target Source:** `my3pai`
   - **Prefix:** `rx:`
   - **Max Entities:** `100` (or 0 for unlimited)
   - **Dry Run:** `false` (for live run)
   - **LLM Temperature:** `0.8`
4. Click **Run**
5. Monitor progress via polling endpoint

### Stop from UI

Click the **Stop** button next to the running script. This creates a `.stop` file that the script checks between batches.

---

## Test Results

### Unit Tests (13 tests)

| Test | Status | Description |
|------|--------|-------------|
| `test_rephrase_script_discovered` | ✅ | Registry picks up the script |
| `test_rephrase_script_meta` | ✅ | Correct parameters and metadata |
| `test_make_slug` | ✅ | Regex slug generation |
| `test_strip_html` | ✅ | HTML tag removal |
| `test_rephrase_no_llm_configured` | ✅ | Error when LLM not configured |
| `test_rephrase_dry_run` | ✅ | No DB writes in dry-run mode |
| `test_rephrase_live_creates_entities` | ✅ | Creates my3pai entities |
| `test_rephrase_collision_errors` | ✅ | Errors on source_id collision |
| `test_rephrase_llm_error_handling` | ✅ | Graceful LLM error handling |
| `test_rephrase_stop_file` | ✅ | Honors `.stop` flag |
| `test_rephrase_resume_skips_processed` | ✅ | Skips already-processed entities |
| `test_rephrase_html_stripped` | ✅ | HTML stripped from descriptions |
| `test_rephrase_empty_name_errors` | ✅ | Empty rephrased name treated as error |

### Full Test Suite

**281/282 tests passing** (1 pre-existing rate limit test failure unrelated to this change)

---

## Production Run Results

### Batch 1 (4096 tokens)

- **Entities processed:** 10
- **Entities created:** 10
- **Errors:** 0
- **Avg summary length:** 267 chars
- **Avg description length:** 1101 chars

### Batch 2 (10240 tokens)

- **Entities processed:** 20 (10 new, 10 skipped via resume)
- **Entities created:** 10
- **Errors:** 0
- **Avg summary length:** 251 chars
- **Avg description length:** 1169 chars

### Quality Assessment

✅ **Names:** More descriptive and engaging  
✅ **Summaries:** Rewritten in consistent brand voice  
✅ **Descriptions:** Enriched while preserving factual content  
✅ **HTML:** Stripped, plain text output  
✅ **Empty fields:** Preserved as empty (not invented)

---

## Files Changed

| File | Lines | Purpose |
|------|-------|---------|
| `src/dmo/admin_scripts/rephrase_from_source.py` | 495 | Main admin script |
| `scripts/rephrase.py` | 154 | CLI entry point |
| `migrations/versions/012_add_rephrased_state.py` | 28 | State tracking table |
| `src/dmo/models/database.py` | 22 | My3paiRephrased SQLModel |
| `src/dmo/admin/router.py` | 15 | Stop endpoint |
| `tests/test_rephrase_from_source.py` | 475 | Comprehensive test suite |
| `README.md` | 50 | Documentation update |
| `plans/rephrase-from-source.md` | 250 | This document |

**Total:** ~1,489 lines added

---

## Known Limitations

1. **LLM dependency:** Requires external LLM endpoint (Ollama, OpenAI, etc.)
2. **Rate limiting:** No built-in rate limiting for LLM API (relies on `batch_size` parameter)
3. **Single language:** Prompt optimized for English; may need adjustment for other languages
4. **No quality validation:** Relies on LLM to produce valid JSON; malformed responses counted as errors

---

## Future Enhancements

- [ ] Add quality scoring for rephrased content
- [ ] Support multiple LLM endpoints with fallback
- [ ] Add translation support (rephrase in target language)
- [ ] Batch LLM calls for better throughput
- [ ] Add progress tracking via database (instead of in-memory set)
- [ ] Support for rephrasing additional fields (address, opening_hours, etc.)

---

## Rollback Plan

If issues are discovered:

1. **Delete my3pai entities:**
   ```sql
   DELETE FROM entities WHERE source = 'my3pai';
   ```

2. **Clear state table:**
   ```sql
   DELETE FROM my3pai_rephrased;
   ```

3. **Drop migration (if needed):**
   ```sql
   DROP TABLE my3pai_rephrased;
   ```

Original rexby data remains untouched.

---

## Lessons Learned

1. **Reasoning models need high max_tokens:** Qwen reasoning models consume tokens for `reasoning_content` before generating output. Tested 100, 1000, 4096, 10240 — settled on 10240 for comfortable margin.

2. **Resume mechanism is critical:** For long-running jobs (149k entities), resume support prevents starting over after failure.

3. **File-based stop signal:** Simple but effective. Works across CLI, admin UI, and manual intervention.

4. **LLM prompt engineering matters:** The system prompt heavily influences output quality. Iterated on prompt to balance creativity vs. factual accuracy.

5. **Empty field handling:** Better to preserve empty fields than invent content. The LLM respects "omit if empty" instruction.

6. **Slug generation should be deterministic:** Regex-based slug from rephrased name ensures consistency without LLM dependency.

---

## Contact

For questions or issues, refer to:
- `AGENTS.md` — Project conventions
- `plans/rephrase-from-source.md` — This document
- Admin UI → Scripts page → `rephrase_from_source` documentation
