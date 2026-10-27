"""Test a generated server over real stdio, and its relay against a fake buyer."""
import asyncio
import base64
import json
import re
import sys
from pathlib import Path

import pytest
from aimarket_mcp_packager.packager_core import MCPPackager
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def package():
    return MCPPackager().package("translate.multi@v2", "prod-translate", "Translator", "Translate text",
                                {"type": "object", "required": ["text"],
                                 "properties": {"text": {"type": "string"}}})


def test_non_object_tool_schema_is_rejected():
    with pytest.raises(ValueError, match="object schema"):
        MCPPackager().package("echo@v1", "prod", "Echo", "", {"type": "string"})


def test_legacy_import_keeps_the_install_helper_and_uses_the_working_generator():
    from aimarket_mcp_packager.mcp_packager import MCPPackager as LegacyPackager

    core = LegacyPackager()
    pkg = package()
    assert core.generate_server_source(pkg) == MCPPackager().generate_server_source(pkg)
    script = core.generate_install_script(pkg)
    assert "docker pull" in script and "run --rm -i" in script


def test_generated_dockerfile_contains_the_complete_server_and_stdio_config():
    core, pkg = MCPPackager(), package()
    dockerfile = core.generate_dockerfile(pkg)
    encoded = re.search(r"b64decode\('([A-Za-z0-9+/=]+)'\)", dockerfile)[1]
    assert base64.b64decode(encoded).decode() == core.generate_server_source(pkg)
    assert "pip install" in dockerfile and "aimarket-mcp>=" in dockerfile
    assert "COPY" not in dockerfile  # It builds from this Dockerfile alone.
    config = json.loads(pkg.connection_string)
    other = json.loads(core.generate_claude_desktop_config(pkg))
    args = next(iter(config["mcpServers"].values()))["args"]
    assert args == next(iter(other["mcpServers"].values()))["args"]
    assert "-i" in args and "-d" not in args and "-p" not in args


def test_generated_relay_validates_and_forwards_to_the_configured_capability(monkeypatch):
    source = MCPPackager().generate_server_source(package())
    namespace = {"__name__": "generated_fixture"}
    exec(compile(source, "generated_server.py", "exec"), namespace)
    calls = []
    async def invoke(arguments):
        calls.append(arguments)
        return "receipt"
    namespace["market_invoke"] = invoke
    monkeypatch.setenv("AIMARKET_SOURCE_HUB", "https://provider.example")
    tool = namespace["CONFIG"]["tool"]["name"]
    result = asyncio.run(namespace["call_tool"](tool, {"text": "hi"}))
    assert result[0].text == "receipt"
    assert calls == [{"capability_id": "translate.multi@v2", "product_id": "prod-translate",
                      "source_hub": "https://provider.example", "input": {"text": "hi"}}]
    with pytest.raises(Exception):
        asyncio.run(namespace["call_tool"](tool, {}))
    assert len(calls) == 1


def test_generated_server_initializes_and_lists_over_real_stdio(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(MCPPackager().generate_server_source(package()))
    repo = Path(__file__).resolve().parents[3]
    params = StdioServerParameters(command=sys.executable, args=[str(script)],
                                  env={"PYTHONPATH": str(repo / "aimarket-mcp")})
    async def probe():
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                initialized = await client.initialize()
                assert initialized.serverInfo.name == "translator"
                tools = (await client.list_tools()).tools
                assert len(tools) == 1 and tools[0].name == "translate.multi-v2"
                assert tools[0].inputSchema["required"] == ["text"]
    asyncio.run(probe())
