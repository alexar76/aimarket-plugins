#!/usr/bin/env python3
"""Compat entry for Glama / older docs that still invoke ``mcp_stdio_server.py``.

The real stdio server lives at ``aimarket_mcp_packager.stdio_server``. Glama's
generated image clones this monorepo-style tree to ``/app`` and historically ran:

    python /app/plugins/aimarket-mcp-packager/mcp_stdio_server.py

That path vanished when the entry moved into the package — Glama then died with
``FileNotFoundError`` before ``mcp-proxy`` could send ``initialize``. This shim
keeps the old path working; prefer ``python -m aimarket_mcp_packager.stdio_server``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Glama may install requirements without ``pip install -e .`` — ensure the package
# directory on disk is importable when this file is executed by path.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from aimarket_mcp_packager.stdio_server import main

if __name__ == "__main__":
    main()
