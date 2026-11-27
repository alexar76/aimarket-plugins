"""Source for generated stdio relays; no network activity before a tool call."""

SERVER_SOURCE = '''import asyncio
import base64
import json
import os

from jsonschema import Draft202012Validator
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from aimarket_mcp.tools import market_invoke

CONFIG = json.loads(base64.b64decode("__CONFIG_B64__"))
validator = Draft202012Validator(CONFIG["tool"]["inputSchema"])


async def list_tools():
    return [Tool.model_validate(CONFIG["tool"])]


async def call_tool(name, arguments):
    if name != CONFIG["tool"]["name"]:
        raise ValueError("Unknown tool")
    validator.validate(arguments)
    result = await market_invoke({
        "capability_id": CONFIG["capability_id"],
        "product_id": CONFIG["product_id"],
        "source_hub": os.environ.get("AIMARKET_SOURCE_HUB", ""),
        "input": arguments,
    })
    return [TextContent(type="text", text=result)]


async def _on_list_tools(ctx, params):
    return ListToolsResult(tools=await list_tools())


async def _on_call_tool(ctx, params):
    # A refused or failed call is a tool result the model can read, not a protocol error.
    try:
        return CallToolResult(content=await call_tool(params.name, params.arguments or {}))
    except Exception as exc:
        return CallToolResult(content=[TextContent(type="text", text=str(exc))], isError=True)


# The 2.x SDK: this one server answers MCP 2026-07-28 (server/discover, no handshake) and the
# initialize handshake of the earlier revisions.
server = Server(CONFIG["name"], version="2.2.0", on_list_tools=_on_list_tools, on_call_tool=_on_call_tool)


async def main():
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
'''
