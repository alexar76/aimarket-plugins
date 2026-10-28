"""The console scripts an MCP client can launch."""
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_package_name_is_a_command_for_registry_clients():
    # An MCP-registry client runs `uvx <identifier>` for a PyPI package, i.e. a script named like the
    # package. 2.0.1-2.1.2 shipped only `aimarket-mcp-packager-mcp`, so that launch failed before the
    # handshake (mcp-conformance, 2026-09). Both names start the same server.
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    scripts = project["scripts"]
    target = "aimarket_mcp_packager.stdio_server:main"
    assert scripts[project["name"]] == target
    assert scripts["aimarket-mcp-packager-mcp"] == target
