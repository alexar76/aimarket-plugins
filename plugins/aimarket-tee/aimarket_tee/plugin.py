"""tee plugin for AIMarket Hub."""

from aimarket_hub.plugin import HubPlugin
from aimarket_tee.tee_attestation import *


class TEEPlugin(HubPlugin):
    name = "aimarket-tee"
    version = "2.0.0"
    description = "TEE-attested execution (AWS Nitro Enclaves / Intel TDX) — attestation reports and enclave-signed receipts"
    homepage = "https://github.com/alexar76/aimarket-plugins"
    category = "security"

    def register_routes(self, router):
        from fastapi import APIRouter as _AR
        # Plugin-specific routes will be registered here
        pass
