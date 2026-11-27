#!/usr/bin/env bash
# Glama admin → Build steps: ["bash plugins/aimarket-mcp-packager/scripts/glama_install.sh"]
#
# Installs the 2.x MCP SDK the packager is built on (`mcp.server.mcpserver.MCPServer`).
# The 1.x SDK has no such module, so an older `mcp` left in the environment makes the
# packager die on import; the explicit pin below replaces it.
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
  # Explicit pin first so a prior `mcp` 1.x install cannot stay in place.
  uv pip install 'mcp>=2.3.0,<3'
  uv pip install -r "$PKG/requirements-mcp.txt"
  uv pip install --no-deps -e "$PKG"
elif python3 -m pip --version >/dev/null 2>&1; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  python3 -m pip install --no-cache-dir 'mcp>=2.3.0,<3'
  python3 -m pip install --no-cache-dir -r "$PKG/requirements-mcp.txt"
  python3 -m pip install --no-deps -e "$PKG"
else
  echo "glama_install.sh: need uv or python3 -m pip" >&2
  exit 1
fi

python -c "from mcp.server.mcpserver import MCPServer; print('mcp MCPServer OK')"
