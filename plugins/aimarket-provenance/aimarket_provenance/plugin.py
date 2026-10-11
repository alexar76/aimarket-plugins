"""ProvenancePlugin — AWR/2 work receipts for AI outputs.

Hooks into the hub invoke pipeline to auto-generate provenance receipts.
Registers /attest, /receipt/{id}, /verify/{id} API endpoints.
Exposes provenance capabilities in the .well-known manifest.

Receipts are AWR/2 (``awr/SPEC.md`` 2.0.0): W3C Verifiable Credentials with an
``eddsa-jcs-2022`` Data Integrity proof over RFC 8785 canonical bytes, issued by a
``did:key``.  AWR/1 issuance is gone — SPEC.md §12 requires an implementation never to
issue one — while AWR/1 *verification* stays for receipts already in the database.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from aimarket_hub.plugin import HubPlugin
from aimarket_hub.signing import Signer

from ._awr import AWR_VERSION, CRYPTOSUITE, PROOF_TYPE, did_key_for_signer
from .api import create_provenance_router
from .receipt import ProvenanceReceipt, decimal_string
from .storage import ProvenanceStorage

logger = logging.getLogger(__name__)

DEFAULT_SIGNING_KEY_PATH = "data/provenance_signing_key"
DEFAULT_API_TOKEN_ENV = "AIMARKET_PROVENANCE_API_TOKEN"


def _installed_hub_version() -> str:
    """The version of the hub this plugin is actually running inside.

    Prefers ``aimarket_hub.__version__`` because that is what the running code reports; falls
    back to the installed distribution's metadata, and finally to ``"unknown"``. It never
    raises: a receipt must still be issuable when the hub is imported from a source checkout
    with no distribution installed, and a truthful ``"unknown"`` is better than a confident
    wrong number in a field that is inside the signature.
    """
    try:
        import aimarket_hub

        version = getattr(aimarket_hub, "__version__", "")
        if version:
            return str(version)
    except Exception:  # pragma: no cover - defensive; the hub is a hard dependency
        pass
    try:
        from importlib.metadata import version as _dist_version

        return _dist_version("aimarket-hub")
    except Exception:  # pragma: no cover
        return "unknown"


def _load_or_create_signer() -> tuple[Signer, str]:
    """Load persistent signing key or create one on first run.

    Returns ``(signer, did)``.  The DID is logged for audit: under AWR/2 the issuer
    identifier *is* the key (SPEC.md §5.1), so this string is what a verifier will check
    every receipt against, and operators must back the key file up.
    """
    key_path = os.environ.get(
        "AIMARKET_PROVENANCE_KEY_PATH", DEFAULT_SIGNING_KEY_PATH
    )
    signer = Signer(key_path=key_path)
    did = did_key_for_signer(signer)
    logger.info(
        "Provenance signing key loaded (did:key: %s, fingerprint: %s, path: %s)",
        did,
        signer.public_key_b64,
        key_path,
    )
    return signer, did


class ProvenancePlugin(HubPlugin):
    name = "provenance"
    version = "2.3.0"
    description = (
        "AWR/2 work receipts for AI outputs — W3C Verifiable Credentials with an "
        "eddsa-jcs-2022 Data Integrity proof over RFC 8785, issued by a did:key"
    )
    homepage = "https://verify.modelmarket.dev"
    category = "compliance"

    def __init__(self) -> None:
        self._storage: ProvenanceStorage | None = None
        self._signer: Signer | None = None
        # HISTOR anchoring (anchoring.py). None unless AIMARKET_HISTOR_URL is set.
        self._anchors: Any = None
        self._hub_name = "AIMarket Hub"
        # Read from the installed hub rather than written down here. The literal "3.0.0" sat in
        # this line from the first commit and shipped inside 1.1.0 on PyPI, so every receipt ever
        # issued attested a hub version that was already three releases stale -- and `hubInfo` is
        # covered by the eddsa-jcs-2022 proof, which means the wrong value cannot be corrected
        # afterwards without destroying the signature. Nothing reads the field (no verifier, no
        # spec vector, no verdict path), so this was not a security hole; it was a claim inside a
        # signature that was simply false, on documents handed to auditors.
        self._hub_version = _installed_hub_version()
        self._auto_receipt = True
        self._api_token = os.environ.get(DEFAULT_API_TOKEN_ENV, "")
        self._verify_domain = os.environ.get(
            "AIMARKET_VERIFY_DOMAIN", "https://verify.modelmarket.dev"
        ).rstrip("/")
        receipt_cors = os.environ.get(
            "AIMARKET_RECEIPT_CORS_ORIGINS",
            f"{self._verify_domain},https://use.modelmarket.dev",
        )
        self._receipt_cors_origins = tuple(
            origin.strip().rstrip("/")
            for origin in receipt_cors.split(",")
            if origin.strip()
        )
        self._hub_url = os.environ.get("AIMARKET_HUB_URL", "").rstrip("/")

    def on_startup(self, db: Any) -> None:
        database_url = os.environ.get("DATABASE_URL", "")
        if hasattr(db, "db_path"):
            base_path = db.db_path.parent
            self._storage = ProvenanceStorage(
                str(base_path / "provenance.db"),
                database_url=database_url,
            )
        else:
            self._storage = ProvenanceStorage(database_url=database_url)
        logger.info("Provenance storage initialized at %s", self._storage.db_path)

    def register_routes(self, router: Any) -> None:
        # Load persistent signing key — same key survives restarts
        signer, did = _load_or_create_signer()
        self._signer = signer

        # Configure auth
        api_token = os.environ.get(DEFAULT_API_TOKEN_ENV, "")
        if not api_token:
            logger.warning(
                "No AIMARKET_PROVENANCE_API_TOKEN set — /attest is disabled (503). "
                "Set this env var to enable manual attestation."
            )
        logger.info("Provenance issuing AWR/%s receipts as %s", AWR_VERSION, did)

        provenance_router = create_provenance_router(
            storage=self._storage or ProvenanceStorage(),
            signer=signer,
            hub_name=self._hub_name,
            hub_version=self._hub_version,
            api_token=api_token,
            verify_domain=self._verify_domain,
            receipt_cors_origins=self._receipt_cors_origins,
        )
        self._start_anchoring(signer)

        @provenance_router.get("/anchor/{receipt_id:path}")
        async def anchor_status(receipt_id: str) -> Any:
            """Where this receipt stands in HISTOR's receipts log (anchoring.py).

            A row pruned from the outbox is answered from the log itself, so an anchored
            receipt never reads as "not_queued" once its row has aged out."""
            from fastapi.responses import JSONResponse
            from starlette.concurrency import run_in_threadpool

            receipt = (self._storage or ProvenanceStorage()).get_by_receipt_id(receipt_id)
            if receipt is None:
                return JSONResponse(status_code=404, content={"error": "receipt_unknown"})
            if self._anchors is None:
                return {"receipt_id": receipt_id, "digest_sri": receipt.digest_sri, "status": "not_configured"}
            # Off the event loop: the log lookup can wait seconds on HISTOR.
            state = await run_in_threadpool(self._anchors.status, receipt.digest_sri, ask_log=True)
            return {"receipt_id": receipt_id, "digest_sri": receipt.digest_sri,
                    **(state or {"status": "not_queued"})}

        router.include_router(provenance_router)

    def _start_anchoring(self, signer: Any) -> None:
        from .anchoring import AnchorOutbox, histor_url

        if not histor_url() or self._storage is None:
            return
        try:
            from ._awr import signing_key_from_signer

            flush_s = float(os.environ.get("AIMARKET_HISTOR_FLUSH_S", "30") or 30)
            self._anchors = AnchorOutbox(self._storage._backend, signing_key_from_signer(signer), flush_s=flush_s)
            self._anchors.start()
        except Exception as exc:  # noqa: BLE001 - anchoring is additive; receipts work without it
            logger.error("Provenance: HISTOR anchoring disabled: %s", exc)
            self._anchors = None

    # ── URLs the plugin advertises ─────────────────────────────

    def _receipt_url(self, receipt_id: str) -> str:
        path = "/ai-market/v2/p/provenance/receipt/%s" % (receipt_id,)
        return (self._hub_url + path) if self._hub_url else path

    def _verify_url(self, receipt_id: str) -> str:
        path = "/ai-market/v2/p/provenance/verify/%s" % (receipt_id,)
        return (self._hub_url + path) if self._hub_url else path

    def on_invoke_receipt(
        self, output: dict, context: dict
    ) -> dict | None:
        """Generate AWR/2 after the hub has accepted and finalized an invoke.

        The previous implementation ran inside ``on_invoke_post_check``.  That hook was
        called before the built-in safety gate and before settlement, and the hub passed
        only product/capability ids.  Receipts therefore committed to an empty input,
        ``providerHub=local``, zero latency and zero price — and a receipt could survive
        for an output the next safety gate rejected.  The dedicated final hook receives
        the complete, stable context and returns the compact response envelope directly.
        """
        if not self._auto_receipt or not self._storage:
            return None

        try:
            product_id = context.get("product_id", "")
            capability_id = context.get("capability_id", "")
            model_id = (
                f"{capability_id}@{product_id}" if product_id
                else capability_id
            )
            input_payload = context.get("input", {})
            signer = self._signer or Signer()

            charged_price = context.get("price_usd", 0.0)
            settlement = context.get("settlement")
            if not settlement and context.get("settlement_status") == "captured":
                amount = decimal_string(charged_price)
                settlement = {
                    "scheme": context.get("settlement_scheme", "aimarket-channel-v1"),
                    "holdId": context.get("nonce", ""),
                    "amount": {"currency": "USD", "amount": amount},
                }

            receipt = ProvenanceReceipt.create(
                model_id=model_id,
                provider_hub=context.get("provider_hub", "local"),
                input_payload=input_payload,
                output_payload=output,
                signer=signer,
                hub_name=self._hub_name,
                hub_version=self._hub_version,
                latency_ms=context.get("latency_ms", 0),
                price_usd=charged_price,
                status=context.get("status", "succeeded"),
                invocation_nonce=context.get("nonce"),
                settlement=settlement,
                # Subcontractors' work receipts, as {id, digestSRI} references
                # (aimarket-protocol/mandates.md §6.4): the chain edge commits to their bytes.
                parent_receipts=[
                    p for p in (context.get("parents") or [])
                    if isinstance(p, dict) and isinstance(p.get("digestSRI"), str)
                ] or None,
            )
            self._storage.store(receipt)
            if self._anchors is not None:
                try:
                    self._anchors.enqueue(receipt.digest_sri)
                except Exception as exc:  # noqa: BLE001 - never fail a receipt over its anchor
                    logger.error("Provenance: could not queue %s for anchoring: %s", receipt.receipt_id, exc)

            return {
                "receipt_id": receipt.receipt_id,
                # The hub's own routes. Both exist and both work; the previous
                # `{verify_domain}/r/{short_id}` did not — the static verifier reads no
                # path, so that link opened an empty form (see README).
                "verify_url": self._verify_url(receipt.receipt_id),
                "receipt_url": self._receipt_url(receipt.receipt_id),
                # Offline verification, by anyone, with no dependency on this hub: fetch
                # `receipt_url` and paste the document here (SPEC.md §1.2, §13.5).
                "verifier_url": self._verify_domain,
                "awr_version": receipt.awr_version,
                "issuer": receipt.issuer_id,
                # What a parent receipt's `parents` edge commits to (SPEC.md §8.1).
                "digest_sri": receipt.digest_sri,
            }
        except Exception as exc:
            logger.error("Failed to generate provenance receipt: %s", exc)

        return None  # Never blocks — the caller decides whether proof is required.

    def on_shutdown(self) -> None:
        if self._anchors is not None:
            self._anchors.stop()

    def get_manifest_extension(self) -> dict:
        return {
            "provenance": {
                "version": self.version,
                "receipt_format": "AWR/2 (W3C Verifiable Credential)",
                "awr_version": AWR_VERSION,
                "specification": "https://verify.modelmarket.dev/ns/awr/v2",
                "proof_type": PROOF_TYPE,
                "cryptosuite": CRYPTOSUITE,
                "canonicalization": "RFC 8785 (JCS)",
                "issuer_identity": "did:key",
                "signing_algorithm": "Ed25519",
                "endpoints": {
                    "attest": "/ai-market/v2/p/provenance/attest",
                    "receipt": "/ai-market/v2/p/provenance/receipt/{id}",
                    "verify": "/ai-market/v2/p/provenance/verify/{id}",
                    "anchor": "/ai-market/v2/p/provenance/anchor/{id}",
                },
                "features": {
                    "auto_receipt": self._auto_receipt,
                    "tee_attestation": True,
                    "zk_proofs": True,
                    "provenance_chains": True,
                    # SPEC.md §7.3: an attestation is carried inside the signature and is
                    # NOT verified — that needs the platform's certificate chain, which an
                    # offline verifier must not fetch. Advertising it as verified is the
                    # exact misrepresentation §7.3 was written about.
                    "attestations_verified": False,
                    "legacy_awr1_verification": True,
                    "legacy_awr1_issuance": False,
                    "offline_verifiable": True,
                    # Subcontractors' receipts are committed to in `parents` (AWR/2 §8).
                    "subcontract_parents": True,
                },
                # Where this hub anchors receipt digests (HISTOR receipts log), if anywhere.
                "transparency_log": self._anchors.url if self._anchors is not None else None,
            }
        }
