"""Test a generated server over real stdio, and its relay against a fake buyer."""
import asyncio
import base64
import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest
from aimarket_mcp_packager.packager_core import MCPPackager


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


def _talk(command, env, messages, wait=30):
    """Send JSON-RPC lines to one stdio server, as a host does; return the replies by id."""
    process = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    replies = {}
    try:
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            if "id" not in message:
                continue
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline and message["id"] not in replies:
                if not select.select([process.stdout], [], [], 0.5)[0]:
                    assert process.poll() is None, process.stderr.read()[-2000:]
                    continue
                line = process.stdout.readline()
                assert line, process.stderr.read()[-2000:]
                reply = json.loads(line)
                if reply.get("id") == message["id"]:
                    replies[message["id"]] = reply
            assert message["id"] in replies, f"no reply to {message['method']}"
    finally:
        process.stdin.close()
        process.terminate()
        process.wait(10)
        process.stdout.close()
        process.stderr.close()
    return replies


def _generated_server(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(MCPPackager().generate_server_source(package()))
    repo = Path(__file__).resolve().parents[3]
    return [sys.executable, str(script)], {**os.environ, "PYTHONPATH": str(repo / "aimarket-mcp")}


def test_generated_server_initializes_and_lists_over_real_stdio(tmp_path):
    command, env = _generated_server(tmp_path)
    replies = _talk(command, env, [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "packager-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    initialized = replies[1]["result"]
    assert initialized["protocolVersion"] == "2025-06-18"
    assert initialized["serverInfo"]["name"] == "translator"
    tools = replies[2]["result"]["tools"]
    assert len(tools) == 1 and tools[0]["name"] == "translate.multi-v2"
    assert tools[0]["inputSchema"]["required"] == ["text"]


def test_generated_server_answers_mcp_2026_07_28_without_a_handshake(tmp_path):
    command, env = _generated_server(tmp_path)
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "packager-test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {}}
    replies = _talk(command, env, [
        {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": meta}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"_meta": meta}},
        # Refused by the tool's schema before anything is sent to a hub: a readable tool result.
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "translate.multi-v2", "arguments": {}, "_meta": meta}},
    ])
    found = replies[1]["result"]
    assert "2026-07-28" in found["supportedVersions"]
    assert found["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "translator"
    listed = replies[2]["result"]
    assert listed["resultType"] == "complete"
    assert [tool["name"] for tool in listed["tools"]] == ["translate.multi-v2"]
    refused = replies[3]["result"]
    assert refused["isError"] is True and "text" in refused["content"][0]["text"]


def test_the_stdio_server_announces_the_package_version():
    from aimarket_mcp_packager import __version__
    from aimarket_mcp_packager.stdio_server import mcp

    assert mcp.version == __version__
    assert f'version="{__version__}"' in MCPPackager().generate_server_source(package())
