# Build from aimarket-plugins repo root:
#   docker build -f plugins/aimarket-mcp-packager/Dockerfile.root-context .
FROM python:3.12-slim

LABEL org.opencontainers.image.title="aimarket-mcp-packager"
LABEL ai-market.mcp="true"

WORKDIR /app/packager

COPY plugins/aimarket-mcp-packager/requirements-mcp.txt plugins/aimarket-mcp-packager/pyproject.toml plugins/aimarket-mcp-packager/README.md ./
COPY plugins/aimarket-mcp-packager/aimarket_mcp_packager/ ./aimarket_mcp_packager/
COPY plugins/aimarket-mcp-packager/mcp_stdio_server.py ./

RUN pip install --no-cache-dir -r requirements-mcp.txt \
    && pip install --no-cache-dir --no-deps -e .

ENTRYPOINT ["python", "mcp_stdio_server.py"]
