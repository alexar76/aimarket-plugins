# Glama listing — aimarket-plugins (MCP Packager)

Public page: [glama.ai/mcp/servers/alexar76/aimarket-plugins](https://glama.ai/mcp/servers/alexar76/aimarket-plugins)

Admin Dockerfile form: [glama.ai/mcp/servers/alexar76/aimarket-plugins/admin/dockerfile](https://glama.ai/mcp/servers/alexar76/aimarket-plugins/admin/dockerfile)

## What Glama runs

Glama **generates** its own image (clone into `/app`, wrap CMD with `mcp-proxy --`). The repo-root [`Dockerfile`](../../Dockerfile) (from `Dockerfile.root-context` on publish) is for local/self-host only.

Health check: container starts and answers JSON-RPC `initialize` + `tools/list` over **stdio**.

### Form values (copy-paste)

| Field | Value |
|-------|-------|
| **Build steps** | `["bash plugins/aimarket-mcp-packager/scripts/glama_install.sh"]` |
| **CMD arguments** | `[".venv/bin/python", "plugins/aimarket-mcp-packager/mcp_stdio_server.py"]` — do **not** put `mcp-proxy` here |
| **Pinned commit SHA** | empty — use **`main`** (squashed satellite mirror deletes old SHAs) |
| **Environment variables** | optional `AIMARKET_HUB_URL` — not required for introspection |

Do **not** leave Build steps empty or set them to a bare `pip/uv install mcp` — that pulls **mcp 2.x**, which removed `mcp.server.fastmcp` (`FastMCP` → `MCPServer`). The install script pins `mcp>=1.6,<2` into `.venv`.

### Common errors

| Log | Fix |
|-----|-----|
| `No module named 'mcp.server.fastmcp'` | Build steps must run `glama_install.sh` (mcp&lt;2). Sync Server after push. |
| `can't open file '.../mcp_stdio_server.py'` | File is a shim on `main` — clear stale SHA pin, Sync Server. |
| `Connection closed` / `-32000` | Same as above — process exited before initialize. |
| `externally managed environment` / `No module named pip` | Use the script (uv + `.venv`), not system `pip`. |

## Local probe

```bash
# from aimarket-plugins repo root
bash plugins/aimarket-mcp-packager/scripts/glama_install.sh
.venv/bin/python plugins/aimarket-mcp-packager/mcp_stdio_server.py
```

Or Docker (repo Dockerfile, already pins mcp&lt;2 via requirements-mcp.txt):

```bash
docker build -t aimarket-mcp-packager .
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | docker run --rm -i aimarket-mcp-packager
```
