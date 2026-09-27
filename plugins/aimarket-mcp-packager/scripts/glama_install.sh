#!/usr/bin/env bash
# Glama admin → Build steps: ["bash plugins/aimarket-mcp-packager/scripts/glama_install.sh"]
#
# Must pin mcp<2: 2.x renames FastMCP → MCPServer and makes `mcp.server.fastmcp`
# raise ModuleNotFoundError ("pin mcp<2 to keep running v1 code"). Glama's bare
# `pip/uv install mcp` pulls 2.x and the packager dies on import.
#
# Glama debian + recent CPython: no system pip / PEP 668 — use a venv.
set -euo pipefail

# This file lives at plugins/aimarket-mcp-packager/scripts/ — repo root is ../../..
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PKG="$ROOT/plugins/aimarket-mcp-packager"
cd "$ROOT"

if [[ ! -f "$PKG/requirements-mcp.txt" ]]; then
  echo "glama_install.sh: missing $PKG/requirements-mcp.txt (cwd=$ROOT)" >&2
  exit 1
fi

if command -v uv >/dev/null 2>&1; then
  uv venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  # Explicit pin first so a prior unscoped `mcp` install cannot stick at 2.x.
  uv pip install 'mcp>=1.6,<2'
  uv pip install -r "$PKG/requirements-mcp.txt"
  uv pip install --no-deps -e "$PKG"
elif python3 -m pip --version >/dev/null 2>&1; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python3 -m pip install --no-cache-dir 'mcp>=1.6,<2'
  python3 -m pip install --no-cache-dir -r "$PKG/requirements-mcp.txt"
  python3 -m pip install --no-deps -e "$PKG"
else
  echo "glama_install.sh: need uv or python3 -m pip" >&2
  exit 1
fi

python -c "from mcp.server.fastmcp import FastMCP; print('mcp FastMCP OK')"
