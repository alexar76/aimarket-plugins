"""Exercise real FastMCP introspection, including aliases local to the registrar."""
import asyncio
import json

import pytest
from jsonschema import Draft202012Validator
from mcp.server.fastmcp import FastMCP


def test_all_oracle_tools_register_with_valid_constrained_schemas():
    # Importing used to fail before initialize with InvalidSignature/ValuesList.
    from aimarket_oracle_gateway.gateway_core import CAPABILITIES
    from aimarket_oracle_gateway.stdio_server import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert set(tools) == set(CAPABILITIES) | {"list_oracle_capabilities"}
    for tool in tools.values():
        Draft202012Validator.check_schema(tool.inputSchema)
    schema = tools["aggregate_values"].inputSchema
    validator = Draft202012Validator(schema)
    validator.validate({"values": [1.0, 2.0], "trim": 0.1})
    assert not validator.is_valid({"values": [], "trim": 0.1})
    assert not validator.is_valid({"values": [1.0], "trim": 0.5})
    assert tools["optimize_route"].inputSchema["properties"]["points"]["minItems"] == 3


@pytest.mark.parametrize("name,arguments,required_payload", [
    ("sortes_draw", {"alpha": "seed"}, {"alpha": "seed", "num_bytes": 32}),
    ("sortes_verify", {"alpha": "seed", "pi": "proof", "public_key": "pk"}, {"pi": "proof"}),
    ("gauss_field", {"X": [[0]], "y": [1], "query": [[1]]}, {"query": [[1.0]]}),
    ("gauss_suggest", {"X": [[0]], "y": [1], "bounds": [[0, 1]]}, {"bounds": [[0.0, 1.0]]}),
    ("gauss_verify", {"X": [[0]], "y": [1], "query": [[1]], "claimed_mean": [1]}, {"claimed_mean": [1.0]}),
    ("aestus_seal", {"data": "hello"}, {"data": "hello", "T": 1_000_000}),
    ("aestus_open", {"puzzle": {"N": "17"}}, {"puzzle": {"N": "17"}}),
    ("aestus_verify", {"puzzle": {"N": "17"}, "b": "2"}, {"b": "2"}),
    ("betti_homology", {"points": [[0], [1]]}, {"max_dim": 2}),
    ("betti_distance", {"points_a": [[0]], "points_b": [[1]]}, {"dim": 1}),
    ("kantor_transport", {"a": [1], "b": [1], "cost": [[0]]}, {"cost": [[0.0]], "method": "exact"}),
    ("kantor_verify", {"a": [1], "b": [1], "claimed_cost": 0, "plan": [[1]],
                       "potentials": {"u": [0], "v": [0]}}, {"plan": [[1.0]]}),
    ("fourier_spectrum", {"edges": [["a", "b"]]}, {"k": 6}),
    ("fourier_verify", {"edges": [["a", "b"]], "eigenvalue": 2, "vector": [1, -1]}, {"lambda": 2.0}),
])
def test_family_tools_route_validated_arguments_without_network(name, arguments, required_payload):
    from aimarket_oracle_gateway.mcp_family_tools import register_family_tools

    calls = []
    def run(tool, payload):
        calls.append((tool, payload))
        return json.dumps({"ok": True})

    mcp = FastMCP("fixture")
    register_family_tools(mcp, run)
    asyncio.run(mcp.call_tool(name, arguments))
    assert len(calls) == 1 and calls[0][0] == name
    for key, value in required_payload.items():
        assert calls[0][1][key] == value
    # Optional arrays/objects must be omitted, rather than sent as null to handlers
    # that apply defaults only when a field is absent.
    assert all(value is not None for value in calls[0][1].values())
