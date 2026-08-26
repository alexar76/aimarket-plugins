"""streaming plugin for AIMarket Hub."""

from aimarket_hub.plugin import HubPlugin
from aimarket_streaming.streaming import *


class StreamingPlugin(HubPlugin):
    name = "aimarket-streaming"
    version = "2.0.0"
    description = "SSE/WS streaming with per-chunk billing — micro-receipts as tokens arrive, cancel mid-stream and pay only for what was received"
    homepage = "https://github.com/alexar76/aimarket-plugins"
    category = "monetization"

    def register_routes(self, router):
        from fastapi import APIRouter as _AR
        # Plugin-specific routes will be registered here
        pass
    def on_startup(self, db):
        self._biller = None  # Lazy init on first stream
