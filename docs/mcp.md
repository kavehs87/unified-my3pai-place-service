# MCP server (read-only)

The service can expose its Read API as a [Model Context Protocol](https://modelcontextprotocol.io) server over Streamable HTTP at `POST /mcp`. It is a thin adapter over the same services and Redis caches as the REST API: no write tools, no new query engine, no schema changes.

**Status:** disabled by default. Enable with `MCP_ENABLED=true`.

## Tools (7, all read-only)

| Tool | What it does | REST equivalent |
|---|---|---|
| `search_places` | Name search with exact filters and soft location bias | `GET /search` |
| `find_nearby` | Distance-sorted places within a radius | `GET /nearby` |
| `map_bounding_box` | Places inside a viewport bbox | `GET /map` |
| `get_place` | Full detail (media, classifications, attributes, live open status) | `GET /{source}/{source_id}` |
| `list_unified_categories` | Taxonomy tree with counts (call before filtering) | `GET /unified-categories` |
| `list_classifications` | Taxonomy tags attached to entities | `GET /classifications` |
| `list_classification_categories` | Distinct classification category names | `GET /classifications/categories` |

Conventions:

- `page_size` default 10, hard max 50 (`MCP_DEFAULT_PAGE_SIZE`, `MCP_MAX_PAGE_SIZE`).
- List tools omit `attributes` unless `include_attributes=true`; `get_place` always includes them.
- `get_place` truncates `media` to `MCP_DETAIL_MAX_MEDIA` (20) and `classifications` to `MCP_DETAIL_MAX_CLASSIFICATIONS` (50) and reports `media_total` / `classifications_total`.
- All place text is untrusted third-party data; the server instructions tell agents never to treat it as instructions.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MCP_ENABLED` | `false` | Mount the endpoint |
| `MCP_PATH` | `/mcp` | Public endpoint path |
| `MCP_DEFAULT_PAGE_SIZE` | `10` | Tool page-size default |
| `MCP_MAX_PAGE_SIZE` | `50` | Tool page-size hard cap |
| `MCP_ALLOWED_HOSTS` | `[]` | DNS-rebinding allowlist (JSON list, e.g. `["fw.my3p.ai", "fw.my3p.ai:*"]`). When empty, only localhost hosts are accepted and a startup warning is logged |
| `MCP_ALLOWED_ORIGINS` | `[]` | Origin allowlist (browser-based MCP clients only) |
| `MCP_DETAIL_MAX_MEDIA` | `20` | `get_place` media truncation |
| `MCP_DETAIL_MAX_CLASSIFICATIONS` | `50` | `get_place` classification truncation |

**Production:** set `MCP_ALLOWED_HOSTS` to the public hostname(s) or every request receives `421 Misdirected Request`.

## Client configuration

Claude / Cursor / any Streamable HTTP client:

```json
{ "mcpServers": { "dmo": { "type": "http", "url": "https://fw.my3p.ai/mcp" } } }
```

Clients without native HTTP support:

```json
{
  "mcpServers": {
    "dmo": { "command": "npx", "args": ["-y", "mcp-remote", "https://fw.my3p.ai/mcp"] }
  }
}
```

Inspector (local):

```bash
npx @modelcontextprotocol/inspector
# transport: Streamable HTTP, URL: http://localhost:8000/mcp
```

The endpoint is public and read-only (same data as the public Read API) — no API key is required. Writes remain impossible: no write tool exists and `dmo/mcp/` does not import the write services.

## Behavior notes

- **Stateless JSON mode** (`stateless_http=True`, `json_response=True`): a fresh transport per request, no `Mcp-Session-Id`, correct across the 8 Uvicorn workers with no sticky routing.
- **POST headers:** `Content-Type: application/json` (else `400`) and `Accept` including `application/json` (else `406`; `*/*` is accepted).
- **`GET /mcp`** returns `405` for requests carrying a modern `MCP-Protocol-Version` header (`2026-07-28`); legacy-era GET opens a no-op SSE stream until disconnect.
- `POST /mcp/` redirects (`307`) to `/mcp`. `GET /mcp/mcp` is treated as an entity lookup (`source=mcp`) and 404s.
- Middleware applies: request IDs (`X-Request-ID`), rate limiting, request timeout, Prometheus metrics (`/mcp` is included in `http_requests_total`), plus `mcp_tool_calls_total` and `mcp_tool_call_duration_seconds` per tool.

## Verification

```bash
curl -s http://localhost:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
