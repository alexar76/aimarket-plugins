"""The real stdio entry point must finish registration and answer MCP requests."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1]
STARTUP_TIMEOUT = 20
_LAUNCH = """
import importlib
import runpy
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

def deny_external_actions(event, args):
    if event in {
        'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo',
        'socket.gethostbyname', 'socket.gethostbyname_ex',
        'subprocess.Popen', 'os.system', 'os.posix_spawn',
    }:
        raise PermissionError('startup test denies network and child processes')

sys.addaudithook(deny_external_actions)
package = importlib.import_module('aimarket_oracle_gateway')
if not Path(package.__file__).resolve().is_relative_to(root):
    raise RuntimeError('startup test imported a different Oracle Gateway package')
runpy.run_module('aimarket_oracle_gateway.stdio_server', run_name='__main__', alter_sys=True)
"""


def _startup(source_root: Path, working_directory: Path):
    """List the actual subprocess server; a copied source root also supports negative controls."""
    stderr_path = working_directory / "oracle-startup-stderr.log"
    inbox = queue.Queue()
    deadline = time.monotonic() + STARTUP_TIMEOUT
    with stderr_path.open("wb") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-u", "-c", _LAUNCH, str(source_root)],
            cwd=working_directory,
            env={"PATH": os.defpath},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
        )

        def diagnostics():
            return stderr_path.read_text(encoding="utf-8", errors="replace")[-6000:]

        def read_stdout():
            try:
                for line in iter(lambda: process.stdout.readline(1_000_000), b""):
                    if not line.endswith(b"\n"):
                        inbox.put(ValueError("oversized or incomplete MCP stdout line"))
                        return
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        inbox.put(ValueError("non-JSON data on MCP stdout"))
                        return
                    inbox.put(message)
            finally:
                inbox.put(None)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()

        def send(message):
            try:
                process.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
                process.stdin.flush()
            except OSError as exc:
                raise AssertionError("MCP server closed stdin:\n" + diagnostics()) from exc

        def request(request_id, method, params):
            send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"MCP server timed out answering {method}:\n" + diagnostics())
                try:
                    reply = inbox.get(timeout=remaining)
                except queue.Empty as exc:
                    raise AssertionError(f"MCP server timed out answering {method}:\n" + diagnostics()) from exc
                if reply is None:
                    raise AssertionError(f"MCP server exited before answering {method}:\n" + diagnostics())
                if isinstance(reply, Exception):
                    raise AssertionError(f"Invalid MCP stdout while answering {method}: {reply}\n" + diagnostics())
                assert isinstance(reply, dict), f"MCP reply is not an object: {reply!r}"
                if "id" not in reply and "method" in reply:
                    continue  # A server notification cannot stand in for the requested result.
                assert reply.get("jsonrpc") == "2.0" and type(reply.get("id")) is int
                assert reply["id"] == request_id, f"MCP response ID mismatch: {reply!r}"
                assert "error" not in reply, f"MCP {method} failed: {reply!r}"
                assert isinstance(reply.get("result"), dict), f"MCP {method} has no object result"
                return reply["result"]

        completed = False
        try:
            initialized = request(1, "initialize", {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "oracle-gateway-startup-regression", "version": "1"},
            })
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            tools = []
            cursor = None
            seen_cursors = set()
            for request_id in range(2, 22):
                result = request(request_id, "tools/list", {"cursor": cursor} if cursor else {})
                assert isinstance(result.get("tools"), list), "MCP tools/list omitted the tools array"
                tools.extend(result["tools"])
                cursor = result.get("nextCursor")
                if cursor is None:
                    break
                assert isinstance(cursor, str) and cursor and cursor not in seen_cursors, "MCP pagination did not advance"
                seen_cursors.add(cursor)
            else:
                raise AssertionError("MCP tools/list did not finish within 20 pages")
            completed = True
            return initialized, tools
        finally:
            with suppress(OSError):
                process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            reader.join(timeout=1)
            process.stdout.close()
            if completed:
                assert process.returncode == 0, "MCP server did not exit cleanly:\n" + diagnostics()


def test_real_stdio_server_initializes_and_lists_complete_oracle_catalog(tmp_path):
    from aimarket_oracle_gateway import __version__
    from aimarket_oracle_gateway.gateway_core import CAPABILITIES

    initialized, listed = _startup(SOURCE_ROOT, tmp_path)
    assert initialized["serverInfo"]["name"] == "aimarket-oracle-gateway"
    assert initialized["serverInfo"]["version"] == __version__
    assert initialized["protocolVersion"] in {"2024-11-05", "2025-03-26", "2025-06-18"}
    assert isinstance(initialized["capabilities"].get("tools"), dict)
    names = [tool["name"] for tool in listed]
    assert len(names) == len(set(names)), "MCP server listed duplicate tools"
    assert set(names) == set(CAPABILITIES) | {"list_oracle_capabilities"}
    tools = {tool["name"]: tool for tool in listed}
    # This nested registrar alias caused 0.3.1 to crash before initialize.
    aggregate = tools["aggregate_values"]["inputSchema"]
    assert aggregate["properties"]["values"]["minItems"] == 1
    assert aggregate["properties"]["trim"]["maximum"] == 0.499
    # Registration must also reach the separate family-tool registrar.
    assert "sortes_draw" in tools and "gauss_field" in tools and "fourier_verify" in tools
