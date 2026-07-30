"""channels plugin — payment channel infrastructure."""

import os

from aimarket_hub.plugin import HubPlugin


def _demo_mode() -> bool:
    """Whether channel deposits are credited without on-chain verification.

    Was hardcoded `True`, so a fully configured production hub still advertised
    its channels as demo. Mirrors HubConfig.payment_ready — the same interlocks
    that gate real verification in channels._open_channel — and fails closed
    (demo) if the hub package is not importable.
    """
    try:
        from aimarket_hub.config import HubConfig

        return not HubConfig().payment_ready
    except Exception:
        return os.getenv("AIFACTORY_PAYMENT_VERIFY_STUB", "0") == "1" or os.getenv(
            "AIFACTORY_PROD", ""
        ).strip() != "1"


class ChannelsPlugin(HubPlugin):
    name = "aimarket-channels"
    version = "2.0.0"
    description = "Pre-funded payment channels — off-chain ledger, on-chain settlement"
    homepage = "https://github.com/ai-factory/aimarket-channels"
    category = "infrastructure"

    def get_manifest_extension(self):
        return {
            "channels": {
                "enabled": True,
                "endpoints": {
                    "open": "/ai-market/v2/channel/open",
                    "close": "/ai-market/v2/channel/close",
                },
                "max_deposit_usd": 10_000,
                "expiry_hours": 24,
                "demo_mode": _demo_mode(),
            }
        }
