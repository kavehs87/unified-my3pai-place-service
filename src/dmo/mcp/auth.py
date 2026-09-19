"""Write-tool authentication (Phase 5 placeholder).

Release 1 exposes read-only tools only; write tools stay unregistered until
``MCP_WRITE_TOOLS_ENABLED`` is set and every write call validates the
``X-API-Key`` against ``settings.api_key`` (constant-time compare).
"""

from dmo.config import settings


def write_tools_enabled() -> bool:
    return settings.mcp_write_tools_enabled
