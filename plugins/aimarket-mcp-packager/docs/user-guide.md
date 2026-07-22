# aimarket-mcp-packager — User Guide

## MCP server (stdio)

This package includes a **Model Context Protocol (MCP) server** at [`mcp_stdio_server.py`](../mcp_stdio_server.py) (Python MCP SDK / `FastMCP`). Tools: `package_capability`, `generate_dockerfile`, `generate_claude_desktop_config`. See [README](../README.md) for client config.

```bash
pip install -r requirements-mcp.txt && pip install -e .
python mcp_stdio_server.py
```

## What it does

Package capabilities as MCP servers for Claude Desktop and other MCP clients. Category: **tooling**.

## Installation

```bash
# PyPI package coming soon — for now install from source (run from plugins/aimarket-mcp-packager/):
pip install -e .
aimarket serve
curl http://localhost:9080/ai-market/v2/plugins | jq '.plugins[] | select(.name=="aimarket-mcp-packager")'
```

## Hub integration

Plugins register via setuptools entry point `aimarket.plugins`. After install, restart the hub — routes mount under `/ai-market/v2/p/{plugin_name}/`.

Invoke hooks: none

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ai-market/v2/p/aimarket-mcp-packager/package` | Build MCP manifest + Docker |
| `GET` | `/ai-market/v2/p/aimarket-mcp-packager/package/{id}` | Download package status |

## Configuration

See plugin README for environment variables. Common hub vars:

| Variable | Description |
|----------|-------------|
| `AIMARKET_HUB_URL` | Public hub URL in receipts/manifest |
| `DATABASE_URL` | Optional PostgreSQL (SQLite default) |

## Verify loaded

```bash
curl http://localhost:9080/.well-known/ai-market.json | jq '.plugin_extensions.mcp-packager'
```

## More

- [SDK integration](sdk-integration.md)
- [User cases](user-cases.md)
- [README](../README.md)
