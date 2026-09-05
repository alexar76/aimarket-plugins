# Glama listing — aimarket-plugins (MCP Packager)

Public page: [glama.ai/mcp/servers/alexar76/aimarket-plugins](https://glama.ai/mcp/servers/alexar76/aimarket-plugins)

Admin Dockerfile form: [glama.ai/mcp/servers/alexar76/aimarket-plugins/admin/dockerfile](https://glama.ai/mcp/servers/alexar76/aimarket-plugins/admin/dockerfile)

## What Glama runs

Glama **generates** its own image (clone into `/app`, wrap CMD with `mcp-proxy --`). The repo-root [`Dockerfile`](../Dockerfile) (copied from `Dockerfile.root-context` on publish) is for local/self-host.

Health check: container starts and answers JSON-RPC `initialize` + `tools/list` over **stdio**.

### Form values (copy-paste)

| Field | Value |
|-------|-------|
| **Build steps** | `["pip install -r plugins/aimarket-mcp-packager/requirements-mcp.txt"]` |
| **CMD arguments** | `["python", "plugins/aimarket-mcp-packager/mcp_stdio_server.py"]` — do **not** put `mcp-proxy` here; Glama wraps it |
| **Pinned commit SHA** | empty — use **`main`** (squashed satellite mirror deletes old SHAs) |
| **Environment variables** | optional `AIMARKET_HUB_URL` — not required for introspection |

Preferred module form (after `pip install -e plugins/aimarket-mcp-packager`):

```json
["python", "-m", "aimarket_mcp_packager.stdio_server"]
```

The file `plugins/aimarket-mcp-packager/mcp_stdio_server.py` is a **compat shim** for the path Glama already had in the form. Do not delete it.

## Local probe

```bash
# from aimarket-plugins repo root
pip install -r plugins/aimarket-mcp-packager/requirements-mcp.txt
python plugins/aimarket-mcp-packager/mcp_stdio_server.py
```

Or Docker:

```bash
docker build -t aimarket-mcp-packager .
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | docker run --rm -i aimarket-mcp-packager
```
