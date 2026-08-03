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
from .receipt import ProvenanceReceipt
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
    version = "2.0.0"
    description = (
        "AWR/2 work receipts for AI outputs — W3C Verifiable Credentials with an "
        "eddsa-jcs-2022 Data Integrity proof over RFC 8785, issued by a did:key"
    )
    homepage = "https://verify.modelmarket.dev"
    category = "compliance"

    def __init__(self) -> None:
        self._storage: ProvenanceStorage | None = None
        self._signer: Signer | None = None
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
        )
        router.include_router(provenance_router)

    # ── URLs the plugin advertises ─────────────────────────────

    def _receipt_url(self, receipt_id: str) -> str:
        path = "/ai-market/v2/p/provenance/receipt/%s" % (receipt_id,)
        return (self._hub_url + path) if self._hub_url else path

    def _verify_url(self, receipt_id: str) -> str:
        path = "/ai-market/v2/p/provenance/verify/%s" % (receipt_id,)
        return (self._hub_url + path) if self._hub_url else path

    def on_invoke_post_check(
        self, output: dict, context: dict
    ) -> dict | None:
        """Auto-generate an AWR/2 provenance receipt for every invoke.

        Attaches ``_provenance_receipt`` to the output dict, which the hub's API pops and
        returns as ``provenance_receipt`` (``aimarket_hub/api.py``).  The two keys that
        contract has always carried — ``receipt_id`` and ``verify_url`` — are still there.
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

            receipt = ProvenanceReceipt.create(
                model_id=model_id,
                provider_hub=context.get("provider_hub", "local"),
                input_payload=input_payload,
                output_payload=output,
                signer=signer,
                hub_name=self._hub_name,
                hub_version=self._hub_version,
                latency_ms=context.get("latency_ms", 0),
                price_usd=context.get("price_usd", 0.0),
                status=context.get("status", "succeeded"),
            )
            self._storage.store(receipt)

            output["_provenance_receipt"] = {
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
            }
        except Exception as exc:
            logger.error("Failed to generate provenance receipt: %s", exc)

        return None  # Never blocks — side-effect only

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
                },
            }
        }
