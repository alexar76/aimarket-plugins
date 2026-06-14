#!/usr/bin/env python3
"""Stdio MCP server for Glama / Claude Desktop — AIMarket MCP Packager tools."""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from aimarket_mcp_packager.packager_core import MCPPackager

mcp = FastMCP(
    "aimarket-mcp-packager",
    instructions=(
        "Package AIMarket capabilities as self-hosted MCP server products: "
        "Docker image metadata, MCP manifest, and Claude Desktop config."
    ),
)
_packager = MCPPackager()


@mcp.tool()
def package_capability(
    capability_id: str,
    product_id: str,
    name: str,
    description: str = "",
    input_schema_json: str = "{}",
    registry: str = "aifactory",
) -> str:
    """Build an MCP server package (manifest, docker image name, subscription tiers)."""
    try:
        input_schema = json.loads(input_schema_json) if input_schema_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid input_schema_json: {exc}"})
    if not isinstance(input_schema, dict):
        input_schema = {}
    pkg = _packager.package(
        capability_id=capability_id,
        product_id=product_id,
        name=name,
        description=description,
        input_schema=input_schema,
        registry=registry,
    )
    return json.dumps(
        {
            "docker_image": pkg.docker_image,
            "mcp_manifest": pkg.mcp_manifest,
            "subscription_tiers": pkg.subscription_tiers,
            "connection_string": pkg.connection_string,
        },
        indent=2,
    )


@mcp.tool()
def generate_dockerfile(
    capability_id: str,
    product_id: str,
    name: str,
    description: str = "",
    input_schema_json: str = "{}",
    registry: str = "aifactory",
) -> str:
    """Generate a Dockerfile template for the packaged MCP server."""
    try:
        input_schema = json.loads(input_schema_json) if input_schema_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid input_schema_json: {exc}"})
    if not isinstance(input_schema, dict):
        input_schema = {}
    pkg = _packager.package(
        capability_id, product_id, name, description, input_schema, registry=registry
    )
    return _packager.generate_dockerfile(pkg)


@mcp.tool()
def generate_claude_desktop_config(
    capability_id: str,
    product_id: str,
    name: str,
    description: str = "",
    input_schema_json: str = "{}",
    registry: str = "aifactory",
) -> str:
    """Generate claude_desktop_config.json snippet for docker-run MCP server."""
    try:
        input_schema = json.loads(input_schema_json) if input_schema_json else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"error": f"invalid input_schema_json: {exc}"})
    if not isinstance(input_schema, dict):
        input_schema = {}
    pkg = _packager.package(
        capability_id, product_id, name, description, input_schema, registry=registry
    )
    return _packager.generate_claude_desktop_config(pkg)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
