"""Response shaping, validation and error mapping for MCP tools.

Tool failures are converted to the SDK's ``ToolError`` (an ``isError`` tool
result, never a JSON-RPC transport error). Entity text is third-party data and
is returned as-is; the server instructions mark it untrusted.
"""

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import uuid4

import structlog
from fastapi import HTTPException
from mcp.server.mcpserver.exceptions import ToolError

from dmo.config import settings
from dmo.exceptions import AppError
from dmo.metrics import MCP_TOOL_CALLS, MCP_TOOL_DURATION
from dmo.models.schemas import CursorPaginatedResponse, EntityDetail, EntityListItem

logger = structlog.get_logger()

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def clamp_page_size(page_size: int | None) -> int:
    """Apply the MCP page-size default and hard cap."""
    if page_size is None:
        return settings.mcp_default_page_size
    return max(1, min(page_size, settings.mcp_max_page_size))


def list_item_payload(item: EntityListItem, include_attributes: bool) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    if not include_attributes:
        data.pop("attributes", None)
    return data


def paginated_payload(
    result: CursorPaginatedResponse[EntityListItem],
    include_attributes: bool,
) -> dict[str, Any]:
    return {
        "results": [list_item_payload(item, include_attributes) for item in result.results],
        "total": result.total,
        "next_cursor": result.next_cursor,
        "has_more": result.has_more,
    }


def detail_payload(detail: EntityDetail) -> dict[str, Any]:
    """Full detail with media/classification lists truncated to configured caps."""
    data = detail.model_dump(mode="json")
    media = data.get("media") or []
    classifications = data.get("classifications") or []
    data["media_total"] = len(media)
    data["classifications_total"] = len(classifications)
    data["media"] = media[: settings.mcp_detail_max_media]
    data["classifications"] = classifications[: settings.mcp_detail_max_classifications]
    return data


def _error_text(status_code: int, message: str, call_id: str) -> str:
    return f"[{status_code}] {message} (call {call_id})"


def map_tool_errors(tool_name: str) -> Callable[[F], F]:
    """Decorator: correlate + time each call and map failures to ToolError."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            call_id = str(uuid4())
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except ToolError as exc:
                MCP_TOOL_CALLS.labels(tool=tool_name, status="error").inc()
                message = str(exc)
                if "(call " not in message:
                    message = f"{message} (call {call_id})"
                raise ToolError(message) from exc
            except HTTPException as exc:
                MCP_TOOL_CALLS.labels(tool=tool_name, status="error").inc()
                logger.info(
                    "mcp_tool_client_error",
                    tool=tool_name,
                    call_id=call_id,
                    status_code=exc.status_code,
                )
                raise ToolError(_error_text(exc.status_code, str(exc.detail), call_id)) from exc
            except AppError as exc:
                MCP_TOOL_CALLS.labels(tool=tool_name, status="error").inc()
                logger.info(
                    "mcp_tool_client_error",
                    tool=tool_name,
                    call_id=call_id,
                    status_code=exc.status_code,
                )
                raise ToolError(_error_text(exc.status_code, exc.message, call_id)) from exc
            except Exception:
                MCP_TOOL_CALLS.labels(tool=tool_name, status="error").inc()
                logger.exception("mcp_tool_failed", tool=tool_name, call_id=call_id)
                raise ToolError(
                    f"Internal error while running {tool_name} (call {call_id})"
                ) from None
            else:
                MCP_TOOL_CALLS.labels(tool=tool_name, status="ok").inc()
                return result
            finally:
                MCP_TOOL_DURATION.labels(tool=tool_name).observe(time.perf_counter() - start)

        return wrapper  # type: ignore[return-value]

    return decorator
