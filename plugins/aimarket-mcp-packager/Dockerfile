# AIMarket MCP Packager — stdio MCP server for Glama / Claude Desktop
FROM python:3.12-slim

LABEL org.opencontainers.image.title="aimarket-mcp-packager"
LABEL org.opencontainers.image.description="Package AIMarket capabilities as self-hosted MCP servers"
LABEL ai-market.mcp="true"

WORKDIR /app

COPY requirements-mcp.txt pyproject.toml README.md ./
COPY aimarket_mcp_packager/ ./aimarket_mcp_packager/
COPY mcp_stdio_server.py ./

RUN pip install --no-cache-dir -r requirements-mcp.txt \
    && pip install --no-cache-dir --no-deps -e .

ENTRYPOINT ["python", "mcp_stdio_server.py"]
