"""reputation plugin for AIMarket Hub."""

from aimarket_hub.plugin import HubPlugin
from aimarket_reputation.reputation_oracle import *


class ReputationPlugin(HubPlugin):
    name = "aimarket-reputation"
    version = "2.0.0"
    description = "reputation oracle"
    homepage = "https://github.com/ai-factory/aimarket-reputation"
    category = "reputation"

    def register_routes(self, router):
        # The reputation HTTP API (/reputation/events, /reputation/slashes,
        # /reputation/{hub_url}) is served by the hub core (aimarket_hub.api) and the
        # oracle is canonical in aimarket_hub.reputation_oracle. This plugin is a thin
        # re-export shim and intentionally registers no routes of its own.
        return

    def on_startup(self, db):
        self._oracle = None

    def get_manifest_extension(self) -> dict:
        return {"reputation": {"bond_required": True, "slashing_enabled": True}}
