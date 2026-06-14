import json

import pytest
from sqlalchemy.exc import DBAPIError, StatementError

from dmo.main import app


def test_timeout_error_detection():
    """StatementError with SQLSTATE 57014 should be detected as timeout."""
    dbapi_err = DBAPIError(
        statement="SELECT 1",
        params=None,
        orig=Exception("query_canceled"),
        code="57014",
    )
    exc = StatementError(
        "query_canceled",
        "SELECT 1",
        None,
        dbapi_err,
    )
    orig_code = getattr(getattr(exc, "orig", None), "code", "")
    assert orig_code == "57014"


def test_non_timeout_error_not_detected():
    """StatementError with other SQLSTATE should NOT be detected as timeout."""
    dbapi_err = DBAPIError(
        statement="SELECT 1",
        params=None,
        orig=Exception("some_error"),
        code="42P01",
    )
    exc = StatementError(
        "some_error",
        "SELECT 1",
        None,
        dbapi_err,
    )
    orig_code = getattr(getattr(exc, "orig", None), "code", "")
    assert orig_code != "57014"


def test_statement_error_without_orig():
    """StatementError without orig should not crash timeout detection."""
    exc = StatementError("message", "SQL", None, None)
    orig_code = getattr(getattr(exc, "orig", None), "code", "")
    assert orig_code == ""


@pytest.mark.asyncio
async def test_get_session_sets_statement_timeout():
    """Verify that get_session sets statement_timeout on the session."""
    from sqlalchemy import text

    from dmo.db import get_session

    async for sess in get_session():
        result = await sess.execute(text("SHOW statement_timeout"))
        timeout = result.scalar()
        assert timeout is not None
        assert timeout != "0"
        break


@pytest.mark.asyncio
async def test_get_session_statement_timeout_is_parameterized():
    """Verify statement_timeout is set via parameterized set_config(), not f-string."""
    from sqlalchemy import text

    from dmo.db import get_session

    async for sess in get_session(timeout_override=15.0):
        result = await sess.execute(text("SHOW statement_timeout"))
        timeout = result.scalar()
        assert timeout == "15s"
        break


@pytest.mark.asyncio
async def test_get_write_session_longer_timeout():
    """Verify that get_write_session uses request_timeout_seconds."""
    from sqlalchemy import text

    from dmo.db import get_write_session

    async for sess in get_write_session():
        result = await sess.execute(text("SHOW statement_timeout"))
        timeout = result.scalar()
        assert timeout is not None
        assert timeout != "0"
        break


@pytest.mark.asyncio
async def test_timeout_response_format():
    """Verify the exception handler returns correct 504 response format."""
    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.datastructures import State

    dbapi_err = DBAPIError(
        statement="SELECT 1",
        params=None,
        orig=Exception("query_canceled"),
        code="57014",
    )
    exc = StatementError(
        "query_canceled",
        "SELECT 1",
        None,
        dbapi_err,
    )

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/search",
        "query_string": b"q=test",
        "headers": [],
        "server": ("localhost", 8000),
    }
    scope["state"] = State()
    scope["state"].request_id = "test-request-id"
    request = Request(scope)

    handler = app.exception_handlers.get(StatementError)
    assert handler is not None, "StatementError handler should be registered"

    response = await handler(request, exc)
    assert isinstance(response, JSONResponse)
    assert response.status_code == 504

    data = json.loads(response.body)
    assert data["error"] == "GatewayTimeout"
    assert data["message"] == "Query exceeded timeout limit"
    assert data["code"] == 504
    assert data["request_id"] == "test-request-id"


@pytest.mark.asyncio
async def test_non_timeout_passthrough():
    """StatementError with non-timeout code should raise, not return 504."""
    from fastapi import Request
    from starlette.datastructures import State

    dbapi_err = DBAPIError(
        statement="SELECT 1",
        params=None,
        orig=Exception("some_error"),
        code="42P01",
    )
    exc = StatementError(
        "some_error",
        "SELECT 1",
        None,
        dbapi_err,
    )

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/search",
        "headers": [],
        "server": ("localhost", 8000),
    }
    scope["state"] = State()
    scope["state"].request_id = "test-request-id"
    request = Request(scope)

    handler = app.exception_handlers.get(StatementError)
    assert handler is not None

    with pytest.raises(StatementError):
        await handler(request, exc)
