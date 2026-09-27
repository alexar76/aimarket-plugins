"""FastAPI router for provenance endpoints.

Endpoints under /ai-market/v2/p/provenance/:
  POST   /attest               — create an AWR/2 WorkReceipt (Bearer auth)
  GET    /receipt/{receipt_id}  — retrieve a stored receipt (public)
  GET    /verify/{receipt_id}   — verify a receipt end-to-end (public)

The three routes, their methods and their auth are unchanged by the AWR/1 → AWR/2
migration.  What ``/attest`` accepts changed in exactly one place — ``parent_receipts`` —
and the reason is in ``_resolve_parents``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from ._awr import AWR_VERSION, CRYPTOSUITE, did_key_for_signer
from .receipt import ProvenanceReceipt
from .storage import ProvenanceStorage
from .verifier import verify_receipt

logger = logging.getLogger(__name__)


def _check_auth(authorization: str, api_token: str) -> None:
    """Require Bearer token auth on /attest.

    Fail-closed: if api_token is empty (operator misconfigured), reject all
    requests with 503. Previous behavior of allowing all requests when token
    was unset led to unauthenticated receipt forgery in default deploys.
    """
    if not api_token:
        raise HTTPException(
            status_code=503,
            detail=(
                "Provenance /attest requires AIMARKET_PROVENANCE_API_TOKEN to be set. "
                "Set this env var (with a strong random secret) before exposing the endpoint."
            ),
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = authorization[7:]
    # Constant-time comparison to prevent timing attacks
    import hmac
    if not hmac.compare_digest(token, api_token):
        raise HTTPException(status_code=403, detail="Invalid API token")


def _resolve_parents(storage: ProvenanceStorage, requested: Any) -> list[Any]:
    """Turn requested parents into AWR/2 digest references (``awr/SPEC.md`` §3.2).

    AWR/1 accepted a bare list of receipt identifiers and wrote them straight into the
    document.  That is the forgeable edge of §13.1: the ``id`` it named was itself outside
    the signature, so an intermediary could rename a valid receipt and re-point the chain
    at it without breaking anything.  An AWR/2 edge commits to the parent's exact bytes.

    A caller may therefore pass a full digest reference, or an identifier that **this hub
    can resolve** — the receipt is loaded from storage and its digest computed here.  An
    identifier that resolves to nothing is a 400, not an id-only edge: the hub cannot
    honestly sign a commitment to bytes it has never seen, and §13.5 forbids fetching them.
    """
    if not requested:
        return []
    if not isinstance(requested, list):
        raise HTTPException(
            status_code=400, detail="parent_receipts must be an array"
        )
    resolved: list[Any] = []
    for entry in requested:
        if isinstance(entry, dict):
            resolved.append(entry)
            continue
        if not isinstance(entry, str):
            raise HTTPException(
                status_code=400,
                detail=(
                    "parent_receipts entries must be receipt ids this hub stores, or "
                    "digest references {'id':..., 'digestSRI':'sha256-...'}"
                ),
            )
        parent = storage.get_by_receipt_id(entry)
        if parent is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"parent receipt {entry} is not stored on this hub, so its digest "
                    "cannot be computed. AWR/2 chain edges are content-addressed "
                    "(SPEC.md §3.2, §8.1) and an id-only edge is re-pointable (§13.1); "
                    "supply {'id': ..., 'digestSRI': 'sha256-...'} if the parent lives "
                    "elsewhere."
                ),
            )
        resolved.append(parent)
    return resolved


def create_provenance_router(
    storage: ProvenanceStorage,
    signer: Any,
    hub_name: str = "AIMarket Hub",
    hub_version: str = "3.0.0",
    api_token: str = "",
    # Kept in the signature (the plugin passes it) though no route uses it: the offline
    # verifier UI is advertised through `_provenance_receipt.verifier_url` in plugin.py,
    # not from here.
    verify_domain: str = "https://verify.modelmarket.dev",
    receipt_cors_origins: tuple[str, ...] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["provenance"])
    allowed_receipt_origins = set(receipt_cors_origins or (
        verify_domain.rstrip("/"),
        "https://use.modelmarket.dev",
    ))

    @router.post("/attest")
    async def attest(
        payload: dict[str, Any],
        authorization: str = Header(default=""),
    ) -> dict[str, Any]:
        """Create a self-contained AWR/2 ``WorkReceipt``.

        Requires Bearer token in Authorization header if
        AIMARKET_PROVENANCE_API_TOKEN is configured.
        """
        _check_auth(authorization, api_token)

        missing = [f for f in ["model_id", "input", "output"] if f not in payload]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required fields: {', '.join(missing)}",
            )

        parents = _resolve_parents(storage, payload.get("parent_receipts"))
        try:
            receipt = ProvenanceReceipt.create(
                model_id=payload["model_id"],
                provider_hub=payload.get("provider_hub", hub_name),
                input_payload=payload["input"],
                output_payload=payload["output"],
                signer=signer,
                hub_name=hub_name,
                hub_version=hub_version,
                parent_receipts=parents,
                tee_attestation=payload.get("tee_attestation"),
                zk_input_proof=payload.get("zk_input_proof"),
                zk_output_proof=payload.get("zk_output_proof"),
                latency_ms=payload.get("latency_ms", 0),
                price_usd=payload.get("price_usd", 0.0),
                currency=payload.get("currency", "USD"),
                invocation_nonce=payload.get("invocation_nonce"),
                reputation_score=payload.get("reputation_score"),
                status=payload.get("status", "succeeded"),
                settlement=payload.get("settlement"),
            )
        except ValueError as exc:
            # An issuance the plugin would itself reject is a client error, and the reason
            # code in the message is the whole point of SPEC.md §11.2 — do not swallow it.
            raise HTTPException(status_code=400, detail=str(exc))

        storage.store(receipt)
        logger.info("AWR/2 provenance receipt created: %s", receipt.receipt_id)
        return receipt.to_dict()

    @router.get("/receipt/{receipt_id:path}")
    async def get_receipt(
        receipt_id: str,
        origin: str = Header(default=""),
    ) -> JSONResponse:
        """Retrieve an immutable AWR document with narrow, GET-only CORS.

        The verifier and USE portal need to fetch this public document in the
        caller's browser.  Adding those origins to the hub-wide CORS middleware
        would also authorize cross-origin POSTs to state-changing routes.  The
        receipt endpoint therefore emits ACAO itself only for the two explicit
        read surfaces; every other origin can still navigate to the public JSON
        but cannot read it from script.
        """
        receipt = storage.get_by_receipt_id(receipt_id)
        if not receipt:
            raise HTTPException(status_code=404, detail="Receipt not found")
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable",
            "Vary": "Origin",
            "X-Content-Type-Options": "nosniff",
        }
        if origin in allowed_receipt_origins:
            headers["Access-Control-Allow-Origin"] = origin
        return JSONResponse(
            content=receipt.to_dict(),
            headers=headers,
            media_type="application/vc+ld+json",
        )

    @router.get("/verify/{receipt_id:path}")
    async def verify(receipt_id: str, profile: str | None = None) -> dict[str, Any]:
        """Verify a provenance receipt by ID — all checks (public)."""
        receipt = storage.get_by_receipt_id(receipt_id)
        if not receipt:
            raise HTTPException(status_code=404, detail="Receipt not found")

        # Bind this hub's own identity to its signing key: a stored receipt that claims to
        # be issued by us must carry our real key. Under AWR/2 the key is derived from
        # `issuer.id`, so the pin is on the did:key itself (SPEC.md §5.1).
        try:
            own_did = did_key_for_signer(signer)
        except Exception as exc:  # a broken key file must not 500 a public GET
            logger.error("cannot derive this hub's did:key: %s", exc)
            own_did = ""
        pins = {hub_name: signer.public_key_b64}
        if own_did:
            pins[own_did] = signer.public_key_b64

        # §8.2: chain edges are resolved only against documents the caller supplied — a
        # verifier MUST NOT fetch a parent. These come out of this hub's own storage,
        # which is not the network, and an edge whose parent is absent stays `unresolved`,
        # which is not an error.
        parents = [
            parent
            for parent in (
                storage.get_by_receipt_id(pid) for pid in receipt.parent_receipts
            )
            if parent is not None
        ]

        result = verify_receipt(
            receipt,
            trusted_issuer_keys=pins,
            parents=parents or None,
            profile=profile,
        )
        return {
            "receipt_id": receipt_id,
            "model_id": receipt.model_id,
            "provider_hub": receipt.provider_hub,
            "timestamp": receipt.timestamp,
            "awr_version": receipt.awr_version,
            "issuer": receipt.issuer_id,
            "cryptosuite": receipt.cryptosuite or None,
            "format": "AWR/1 (legacy, verify-only)" if receipt.is_legacy else "AWR/2",
            "verifier": {"awr_version": AWR_VERSION, "cryptosuite": CRYPTOSUITE},
            **result.to_dict(),
        }

    return router
