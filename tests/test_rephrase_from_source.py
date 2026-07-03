import json
import os
from unittest.mock import AsyncMock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from dmo.admin_scripts.rephrase_from_source import (
    RephraseFromSource,
    _make_slug,
    _strip_html,
    _validate_rephrased,
)


@pytest.mark.asyncio
async def test_rephrase_script_discovered():
    from dmo.admin_scripts.registry import discover_scripts

    registry = discover_scripts()
    assert "rephrase_from_source" in registry


@pytest.mark.asyncio
async def test_rephrase_script_meta():
    from dmo.admin_scripts.registry import get_script

    script = get_script("rephrase_from_source")
    assert script is not None
    assert script.meta.name == "rephrase_from_source"
    assert script.meta.category == "Enrich"
    param_names = [p.name for p in script.meta.parameters]
    assert "source" in param_names
    assert "target_source" in param_names
    assert "prefix" in param_names
    assert "dry_run" in param_names
    assert "batch_size" in param_names
    assert "llm_temperature" in param_names


@pytest.mark.asyncio
async def test_make_slug():
    assert _make_slug("Hello World") == "hello-world"
    assert _make_slug("  Multiple   Spaces  ") == "multiple-spaces"
    assert _make_slug("Special!@#Chars") == "specialchars"
    assert _make_slug("Trailing---") == "trailing"
    assert len(_make_slug("x" * 300)) == 255


@pytest.mark.asyncio
async def test_strip_html():
    assert _strip_html("<p>Hello</p>") == "Hello"
    assert _strip_html("<div><p>Test</p></div>") == "Test"
    assert _strip_html("No HTML here") == "No HTML here"
    assert _strip_html("<a href='http://x.com'>Link</a>") == "Link"


@pytest.mark.asyncio
async def test_rephrase_no_llm_configured():
    script = RephraseFromSource()
    params = {"source": "rexby", "dry_run": True}
    result = await script.run(params, db=None, llm=None)
    assert result.success is False
    assert "LLM not configured" in result.message


@pytest.mark.asyncio
async def test_rephrase_dry_run(session: AsyncSession):
    """Test dry run mode - should not create entities."""
    from dmo.models.database import Entity

    # Create test rexby entities
    for i in range(3):
        entity = Entity(
            source="rexby",
            source_id=f"test_{i}",
            name=f"Test Place {i}",
            summary=f"Summary {i}",
            description=f"<p>Description {i}</p>",
            place_type="experience",
            latitude=40.0 + i,
            longitude=10.0 + i,
            is_active=True,
        )
        session.add(entity)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 3,
        "dry_run": True,
        "batch_size": 5,
        "llm_temperature": "1.0",
    }

    # Mock LLM
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": f"Fresh Name {i}",
            "rephrased_summary": f"Fresh Summary {i}",
            "rephrased_description": f"Fresh Description {i}",
        }
    )

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    assert result.affected_count == 3
    assert "would create 3" in result.message

    # Verify no entities were created
    from sqlalchemy import text

    count = (
        await session.execute(text("SELECT COUNT(*) FROM entities WHERE source = 'my3pai'"))
    ).scalar()
    assert count == 0


@pytest.mark.asyncio
async def test_rephrase_live_creates_entities(session: AsyncSession):
    """Test live mode creates my3pai entities."""
    from dmo.models.database import Entity

    # Create test rexby entity
    rexby_entity = Entity(
        source="rexby",
        source_id="live_test_1",
        name="Original Name",
        summary="Original Summary",
        description="<p>Original Description</p>",
        place_type="restaurant",
        latitude=50.0,
        longitude=10.0,
        country="Germany",
        region="Bavaria",
        attributes={"rexby_test": "value"},
        is_active=True,
    )
    session.add(rexby_entity)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 1,
        "dry_run": False,
        "batch_size": 5,
        "db_batch_size": 50,
        "llm_temperature": "1.0",
    }

    # Mock LLM
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": "Rephrased Restaurant Name",
            "rephrased_summary": "A great place to eat",
            "rephrased_description": "This is a wonderful restaurant with amazing food",
        }
    )

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    assert result.affected_count == 1
    assert "created 1" in result.message

    # Verify entity was created
    from sqlalchemy import text

    row = await session.execute(
        text(
            "SELECT source, source_id, name, slug, summary, description, "
            "description_format, place_type, country, attributes "
            "FROM entities WHERE source = 'my3pai' AND source_id = 'rx:live_test_1'"
        )
    )
    row = row.fetchone()
    assert row is not None
    assert row[0] == "my3pai"
    assert row[1] == "rx:live_test_1"
    assert row[2] == "Rephrased Restaurant Name"
    assert row[3] == "rephrased-restaurant-name"
    assert row[4] == "A great place to eat"
    assert row[5] == "This is a wonderful restaurant with amazing food"
    assert row[6] == "text"
    assert row[7] == "restaurant"
    assert row[8] == "Germany"
    attrs = json.loads(row[9]) if isinstance(row[9], str) else row[9]
    assert attrs.get("rexby_test") == "value"

    # Verify state table updated
    state_row = await session.execute(
        text(
            "SELECT source, source_id FROM my3pai_rephrased "
            "WHERE source = 'my3pai' AND source_id = 'rx:live_test_1'"
        )
    )
    state = state_row.fetchone()
    assert state is not None
    assert state[0] == "my3pai"
    assert state[1] == "rx:live_test_1"


@pytest.mark.asyncio
async def test_rephrase_collision_errors(session: AsyncSession):
    """Test that source_id collision causes error."""
    from dmo.models.database import Entity

    # Create existing my3pai entity with same source_id
    existing = Entity(
        source="my3pai",
        source_id="rx:collision_test",
        name="Existing",
        place_type="experience",
        is_active=True,
    )
    session.add(existing)
    await session.flush()

    # Create rexby entity
    rexby = Entity(
        source="rexby",
        source_id="collision_test",
        name="Original",
        place_type="experience",
        is_active=True,
    )
    session.add(rexby)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 1,
        "dry_run": False,
        "batch_size": 5,
        "llm_temperature": "1.0",
    }

    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": "New Name",
            "rephrased_summary": "Summary",
            "rephrased_description": "Description",
        }
    )

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is False
    assert "collision" in result.message.lower()


@pytest.mark.asyncio
async def test_rephrase_llm_error_handling(session: AsyncSession):
    """Test that LLM errors are handled gracefully."""
    from dmo.models.database import Entity

    rexby = Entity(
        source="rexby",
        source_id="llm_error_test",
        name="Test",
        place_type="experience",
        is_active=True,
    )
    session.add(rexby)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 1,
        "dry_run": True,
        "batch_size": 5,
        "llm_temperature": "1.0",
    }

    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = Exception("LLM API error")

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    assert result.affected_count == 0
    assert "errors: 1" in result.message


@pytest.mark.asyncio
async def test_rephrase_stop_file(session: AsyncSession):
    """Test that .stop file triggers graceful stop."""
    from dmo.models.database import Entity

    # Create multiple entities
    for i in range(10):
        entity = Entity(
            source="rexby",
            source_id=f"stop_test_{i}",
            name=f"Place {i}",
            place_type="experience",
            is_active=True,
        )
        session.add(entity)
    await session.flush()

    # Create .stop file
    stop_file = ".stop"
    with open(stop_file, "w") as f:
        f.write("stop")

    try:
        script = RephraseFromSource()
        params = {
            "source": "rexby",
            "target_source": "my3pai",
            "prefix": "rx:",
            "max_entities": 10,
            "dry_run": True,
            "batch_size": 5,
            "llm_temperature": "1.0",
        }

        mock_llm = AsyncMock()
        mock_llm.chat.return_value = json.dumps(
            {
                "rephrased_name": "Name",
                "rephrased_summary": "Summary",
                "rephrased_description": "Description",
            }
        )

        result = await script.run(params, session, llm=mock_llm)
        assert result.success is True
        assert "STOPPED" in result.message
    finally:
        if os.path.exists(stop_file):
            os.remove(stop_file)


@pytest.mark.asyncio
async def test_rephrase_resume_skips_processed(session: AsyncSession):
    """Test that resume skips already-processed entities."""
    from sqlalchemy import text

    from dmo.models.database import Entity

    # Create rexby entities first
    for i in range(3):
        entity = Entity(
            source="rexby",
            source_id=f"resume_{i}",
            name=f"Place {i}",
            place_type="experience",
            is_active=True,
        )
        session.add(entity)
    await session.flush()

    # Create a dummy my3pai entity to use as the "already processed" reference
    dummy = Entity(
        source="my3pai",
        source_id="rx:resume_0",
        name="Dummy",
        place_type="experience",
        is_active=True,
    )
    session.add(dummy)
    await session.flush()

    # Now insert state table entry pointing to the dummy entity
    await session.execute(
        text(
            "INSERT INTO my3pai_rephrased (source, source_id, entity_id) "
            f"VALUES ('my3pai', 'rx:resume_0', '{dummy.id}')"
        )
    )
    await session.commit()

    # Verify state table has the entry
    state_check = await session.execute(
        text("SELECT source_id FROM my3pai_rephrased WHERE source = 'my3pai'")
    )
    state_rows = state_check.fetchall()
    assert len(state_rows) == 1
    assert state_rows[0][0] == "rx:resume_0"

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 3,
        "dry_run": True,
        "batch_size": 5,
        "llm_temperature": "1.0",
    }

    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": "Name",
            "rephrased_summary": "Summary",
            "rephrased_description": "Description",
        }
    )

    # Debug: check what the script sees
    resume_sql = text("SELECT source_id FROM my3pai_rephrased WHERE source = 'my3pai'")
    resume_result = await session.execute(resume_sql)
    state_ids = [row[0] for row in resume_result.fetchall()]
    print(f"State table has: {state_ids}")

    # Check what entities exist
    ent_check = await session.execute(
        text("SELECT source_id FROM entities WHERE source = 'rexby' ORDER BY source_id")
    )
    ent_rows = ent_check.fetchall()
    print(f"Rexby entities: {[row[0] for row in ent_rows]}")

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    # Should skip resume_0, process resume_1 and resume_2
    assert result.affected_count == 2, (
        f"Expected 2, got {result.affected_count}. Message: {result.message}"
    )


@pytest.mark.asyncio
async def test_rephrase_html_stripped(session: AsyncSession):
    """Test that HTML is stripped from description."""
    from dmo.models.database import Entity

    rexby = Entity(
        source="rexby",
        source_id="html_test",
        name="Test",
        description="<p>Original <strong>HTML</strong> description</p>",
        place_type="experience",
        is_active=True,
    )
    session.add(rexby)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 1,
        "dry_run": False,
        "batch_size": 5,
        "llm_temperature": "1.0",
    }

    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": "Test",
            "rephrased_summary": "Summary",
            "rephrased_description": "<p>This has HTML</p> that should be stripped",
        }
    )

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True

    from sqlalchemy import text

    row = await session.execute(
        text("SELECT description FROM entities WHERE source_id = 'rx:html_test'")
    )
    desc = row.scalar()
    assert "<p>" not in desc
    assert "This has HTML" in desc


@pytest.mark.asyncio
async def test_rephrase_empty_name_errors(session: AsyncSession):
    """Test that empty rephrased name is treated as error."""
    from dmo.models.database import Entity

    rexby = Entity(
        source="rexby",
        source_id="empty_name_test",
        name="Test",
        place_type="experience",
        is_active=True,
    )
    session.add(rexby)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 1,
        "dry_run": True,
        "batch_size": 5,
        "llm_temperature": "1.0",
    }

    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": "",
            "rephrased_summary": "Summary",
            "rephrased_description": "Description",
        }
    )

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    assert result.affected_count == 0
    assert "errors: 1" in result.message


@pytest.mark.asyncio
async def test_validate_rephrased_good():
    """Test quality validation with good output."""
    rephrased = {
        "rephrased_name": "This is a good name that meets the minimum length requirement",
        "rephrased_summary": "This is a good summary that captures the essence of the place",
        "rephrased_description": "This is a detailed description with enough content to pass validation. It has multiple paragraphs and provides comprehensive information about the tourism entity including location, features, and visitor tips.",
    }
    result = _validate_rephrased({}, rephrased)
    assert result["valid"] is True
    assert result["issues"] == []
    assert result["name_length"] >= 50
    assert result["summary_length"] >= 50
    assert result["description_length"] >= 200


@pytest.mark.asyncio
async def test_validate_rephrased_empty_name():
    """Test quality validation with empty name."""
    rephrased = {
        "rephrased_name": "",
        "rephrased_summary": "Summary",
        "rephrased_description": "Description",
    }
    result = _validate_rephrased({}, rephrased)
    assert result["valid"] is False
    assert "empty_name" in result["issues"]


@pytest.mark.asyncio
async def test_validate_rephrased_name_too_short():
    """Test quality validation with name too short."""
    rephrased = {
        "rephrased_name": "Short",
        "rephrased_summary": "Summary",
        "rephrased_description": "Description",
    }
    result = _validate_rephrased({}, rephrased)
    assert result["valid"] is False
    assert any("name_too_short" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_validate_rephrased_name_too_long():
    """Test quality validation with name too long."""
    rephrased = {
        "rephrased_name": "x" * 200,
        "rephrased_summary": "Summary",
        "rephrased_description": "Description",
    }
    result = _validate_rephrased({}, rephrased)
    assert result["valid"] is False
    assert any("name_too_long" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_validate_rephrased_summary_too_short():
    """Test quality validation with summary too short."""
    rephrased = {
        "rephrased_name": "x" * 60,
        "rephrased_summary": "Short",
        "rephrased_description": "Description",
    }
    result = _validate_rephrased({}, rephrased)
    assert result["valid"] is False
    assert any("summary_too_short" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_validate_rephrased_description_too_short():
    """Test quality validation with description too short."""
    rephrased = {
        "rephrased_name": "x" * 60,
        "rephrased_summary": "x" * 60,
        "rephrased_description": "Short",
    }
    result = _validate_rephrased({}, rephrased)
    assert result["valid"] is False
    assert any("description_too_short" in issue for issue in result["issues"])


@pytest.mark.asyncio
async def test_rephrase_concurrency_parameter():
    """Test that concurrency parameter is parsed correctly."""
    from dmo.admin_scripts.registry import get_script

    script = get_script("rephrase_from_source")
    assert script is not None
    param_names = [p.name for p in script.meta.parameters]
    assert "concurrency" in param_names
    
    concurrency_param = next(p for p in script.meta.parameters if p.name == "concurrency")
    assert concurrency_param.default == 5
    assert concurrency_param.type == "int"


@pytest.mark.asyncio
async def test_rephrase_concurrency_3(session: AsyncSession):
    """Test processing with concurrency=3."""
    from dmo.models.database import Entity

    # Create test entities
    for i in range(5):
        entity = Entity(
            source="rexby",
            source_id=f"concurrency_3_{i}",
            name=f"Test Place {i}",
            summary=f"Summary {i}",
            description=f"Description {i}",
            place_type="experience",
            is_active=True,
        )
        session.add(entity)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 5,
        "dry_run": True,
        "batch_size": 5,
        "db_batch_size": 50,
        "llm_temperature": "1.0",
        "concurrency": 3,
    }

    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": "Fresh Name That Meets The Minimum Length Requirement Now",
            "rephrased_summary": "Fresh Summary That Captures The Essence Of This Great Place",
            "rephrased_description": "Fresh Description With Enough Content To Pass Validation. It Has Multiple Paragraphs And Provides Comprehensive Information About The Tourism Entity Including Location Features And Visitor Tips For Travelers.",
        }
    )

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    assert result.affected_count == 5
    assert "would create 5" in result.message


@pytest.mark.asyncio
async def test_rephrase_concurrency_5(session: AsyncSession):
    """Test processing with concurrency=5."""
    from dmo.models.database import Entity

    # Create test entities
    for i in range(10):
        entity = Entity(
            source="rexby",
            source_id=f"concurrency_5_{i}",
            name=f"Test Place {i}",
            summary=f"Summary {i}",
            description=f"Description {i}",
            place_type="experience",
            is_active=True,
        )
        session.add(entity)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 10,
        "dry_run": True,
        "batch_size": 5,
        "db_batch_size": 50,
        "llm_temperature": "1.0",
        "concurrency": 5,
    }

    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps(
        {
            "rephrased_name": "Fresh Name That Meets The Minimum Length Requirement Now",
            "rephrased_summary": "Fresh Summary That Captures The Essence Of This Great Place",
            "rephrased_description": "Fresh Description With Enough Content To Pass Validation. It Has Multiple Paragraphs And Provides Comprehensive Information About The Tourism Entity Including Location Features And Visitor Tips For Travelers.",
        }
    )

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    assert result.affected_count == 10
    assert "would create 10" in result.message


@pytest.mark.asyncio
async def test_rephrase_concurrency_with_errors(session: AsyncSession):
    """Test processing with mixed successful and failed LLM calls."""
    from dmo.models.database import Entity

    # Create test entities
    for i in range(5):
        entity = Entity(
            source="rexby",
            source_id=f"concurrency_err_{i}",
            name=f"Test Place {i}",
            summary=f"Summary {i}",
            description=f"Description {i}",
            place_type="experience",
            is_active=True,
        )
        session.add(entity)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 5,
        "dry_run": True,
        "batch_size": 5,
        "db_batch_size": 50,
        "llm_temperature": "1.0",
        "concurrency": 3,
    }

    # First 3 calls succeed, last 2 fail
    call_count = [0]
    
    def mock_chat(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 3:
            return json.dumps({
                "rephrased_name": "Fresh Name That Meets The Minimum Length Requirement Now",
                "rephrased_summary": "Fresh Summary That Captures The Essence Of This Great Place",
                "rephrased_description": "Fresh Description With Enough Content To Pass Validation. It Has Multiple Paragraphs And Provides Comprehensive Information About The Tourism Entity Including Location Features And Visitor Tips For Travelers.",
            })
        else:
            raise Exception("LLM API error")

    mock_llm = AsyncMock()
    mock_llm.chat.side_effect = mock_chat

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is True
    assert result.affected_count == 3
    assert "errors: 2" in result.message


@pytest.mark.asyncio
async def test_rephrase_quality_fallback(session: AsyncSession):
    """Test that quality check triggers fallback."""
    from dmo.models.database import Entity

    # Create test entities
    for i in range(5):
        entity = Entity(
            source="rexby",
            source_id=f"quality_fb_{i}",
            name=f"Test Place {i}",
            summary=f"Summary {i}",
            description=f"Description {i}",
            place_type="experience",
            is_active=True,
        )
        session.add(entity)
    await session.flush()

    script = RephraseFromSource()
    params = {
        "source": "rexby",
        "target_source": "my3pai",
        "prefix": "rx:",
        "max_entities": 5,
        "dry_run": True,
        "batch_size": 5,
        "db_batch_size": 50,
        "llm_temperature": "1.0",
        "concurrency": 5,
    }

    # All entities have empty names (quality failure)
    mock_llm = AsyncMock()
    mock_llm.chat.return_value = json.dumps({
        "rephrased_name": "",
        "rephrased_summary": "Summary",
        "rephrased_description": "Description",
    })

    result = await script.run(params, session, llm=mock_llm)
    assert result.success is False
    assert "Quality check failed" in result.message
    assert "fallback" in str(result.details).lower()


@pytest.mark.asyncio
async def test_rephrase_opencode_zen_config():
    """Test that OpenCode Zen constants are defined."""
    from dmo.admin_scripts.rephrase_from_source import (
        OPENCODE_ZEN_API_KEY,
        OPENCODE_ZEN_BASE_URL,
        DEFAULT_MODEL,
        DEFAULT_MAX_TOKENS,
    )

    assert OPENCODE_ZEN_API_KEY.startswith("sk-")
    assert "opencode.ai" in OPENCODE_ZEN_BASE_URL
    assert DEFAULT_MODEL == "deepseek-v4-flash"
    assert DEFAULT_MAX_TOKENS == 10240
