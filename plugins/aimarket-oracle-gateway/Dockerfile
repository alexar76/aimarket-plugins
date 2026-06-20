# AIMarket Oracle Gateway — stdio MCP server for Glama / Claude Desktop
FROM python:3.12-slim

LABEL org.opencontainers.image.title="aimarket-oracle-gateway"
LABEL org.opencontainers.image.description="Verifiable oracle services (Platon VRF / Chronos VDF / LUMEN reputation) as MCP tools"
LABEL ai-market.mcp="true"

WORKDIR /app

COPY requirements-mcp.txt pyproject.toml README.md ./
COPY aimarket_oracle_gateway/ ./aimarket_oracle_gateway/
COPY mcp_stdio_server.py ./

RUN pip install --no-cache-dir -r requirements-mcp.txt \
    && pip install --no-cache-dir --no-deps -e .

ENTRYPOINT ["python", "mcp_stdio_server.py"]
