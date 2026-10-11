"""data-cap plugin for AIMarket Hub.

One route, and it is the only one this plugin can honestly serve. The docs used to advertise
`/index` (register a private corpus) and `/search` (paid semantic search) — neither was ever
written, and neither belongs here: the hub hosts no corpus and runs no index, so a publisher's
data lives behind the publisher's own `invoke_url`. What the hub can do is turn a description
of that corpus into the listing its own `/supply/register` expects, with the pricing, schemas
and revenue split filled in consistently.

The split is not advisory any more. `AIMARKET_PUBLISHER_SHARE_BPS` is enforced by the hub on
every completed sale and paid into the publisher's credit balance, which is what the old
70/30 arithmetic in this module only described.
"""

from typing import Any

from aimarket_hub.plugin import HubPlugin

from aimarket_data_cap.data_capability import (  # noqa: F401
    DEFAULT_OWNER_SHARE_PCT,
    corpus_fingerprint,
    data_capability_manifest,
    expected_owner_earnings,
    slugify,
)


class DataCapPlugin(HubPlugin):
    name = "aimarket-data-cap"
    version = "3.0.0"
    description = (
        "Data as a capability — build the listing for a private corpus the publisher serves, "
        "with the owner's revenue share enforced by the hub"
    )
    homepage = "https://github.com/alexar76/aimarket-plugins"
    category = "monetization"

    def register_routes(self, router: Any) -> None:
        from fastapi import APIRouter, HTTPException
        from pydantic import BaseModel, Field

        class ManifestRequest(BaseModel):
            name: str = Field(..., min_length=1, max_length=120)
            description: str = Field("", max_length=2000)
            invoke_url: str = Field(..., min_length=8, max_length=2048)
            query_price_usd: float = Field(..., ge=0)
            publisher_id: str = Field("", max_length=120)
            corpus_sha256: str = Field("", max_length=64)
            document_count: int | None = Field(None, ge=0)
            tags: list[str] = Field(default_factory=list)

        sub = APIRouter()

        @sub.post("/manifest")
        def build_manifest(body: ManifestRequest) -> dict[str, Any]:
            """Description of a corpus → a body `/supply/register` accepts.

            Registers nothing itself: publishing is authenticated (an API key or a publisher
            credential) and this endpoint is not the place to smuggle that past the gate.
            """
            if not body.invoke_url.lower().startswith(("http://", "https://")):
                raise HTTPException(
                    status_code=400,
                    detail="invoke_url must be the http(s) endpoint that answers queries",
                )
            manifest = data_capability_manifest(
                name=body.name,
                description=body.description,
                invoke_url=body.invoke_url,
                query_price_usd=body.query_price_usd,
                publisher_id=body.publisher_id,
                corpus_hash=body.corpus_sha256,
                document_count=body.document_count,
                tags=body.tags,
            )
            return {
                "manifest": manifest,
                "next_step": "POST it to /ai-market/v2/supply/register with your X-API-Key",
                "earnings_example": expected_owner_earnings(body.query_price_usd, 1000),
            }

        router.include_router(sub)
