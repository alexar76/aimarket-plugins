"""AIMarket Oracle Gateway — a passthrough MCP server that exposes the ecosystem's
oracle capabilities (Platon VRF, Chronos VDF, LUMEN reputation) as MCP tools so external
AI agents can discover and consume them. See README.md."""

from .gateway_core import CAPABILITIES, CapabilitySpec, GatewayError, OracleGateway

__all__ = ["OracleGateway", "CapabilitySpec", "CAPABILITIES", "GatewayError"]
__version__ = "0.1.0"
