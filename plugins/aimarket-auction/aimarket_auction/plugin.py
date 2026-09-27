"""auction plugin for AIMarket Hub."""

from aimarket_hub.plugin import HubPlugin
from aimarket_auction.spot_auction import *


class AuctionPlugin(HubPlugin):
    name = "aimarket-auction"
    version = "2.0.1"
    description = "Spot bidding market — post a task, providers bid in real time, the consumer picks the winner"
    homepage = "https://github.com/alexar76/aimarket-plugins"
    category = "monetization"

    def register_routes(self, router):
        from fastapi import APIRouter as _AR
        # Plugin-specific routes will be registered here
        pass
