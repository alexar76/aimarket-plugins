"""Shared fixtures.

The AWR/1 document builder lives here rather than in the package because SPEC.md §12
forbids an implementation from issuing AWR/1 and this plugin therefore has no code that
can produce one.  Stored AWR/1 receipts still have to be *verifiable* (§12, and the whole
point of keeping the legacy path), so the tests reconstruct one the way the pre-migration
issuer did: an ``Ed25519Signature2018`` proof whose base64 ``proofValue`` covers the
``key:value|key:value`` rendering of ``credentialSubject`` **only**.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest

from aimarket_hub.signing import Signer
from aimarket_provenance.legacy import (
    compute_hash,
    credential_subject_canonical,
    public_key_to_jwk,
)

AWR1_CONTEXT = [
    "https://www.w3.org/2018/credentials/v1",
    "https://verify.aimarket.org/schemas/provenance-receipt.json",
]


@pytest.fixture
def signer() -> Signer:
    """A signer with a fresh ephemeral key."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Signer(key_path=str(Path(tmp) / "test_key"))


@pytest.fixture
def other_signer() -> Signer:
    with tempfile.TemporaryDirectory() as tmp:
        yield Signer(key_path=str(Path(tmp) / "other_key"))


@pytest.fixture
def sample_input() -> dict:
    return {"prompt": "What is the capital of France?", "temperature": 0.7}


@pytest.fixture
def sample_output() -> dict:
    return {"response": "The capital of France is Paris.", "tokens": 12}


def build_awr1_document(
    signer: Signer,
    *,
    model_id: str = "claude-sonnet-4@anthropic",
    provider_hub: str = "https://hub.aimarket.org",
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    latency_ms: int = 2340,
    price_usd: float = 0.15,
    timestamp: str = "2026-01-15T09:00:00Z",
    parent_receipts: list[str] | None = None,
    tee_attestation: dict[str, Any] | None = None,
    receipt_id: str | None = None,
) -> dict[str, Any]:
    """One AWR/1 document, byte-compatible with what this plugin used to sign."""
    subject: dict[str, Any] = {
        "modelId": model_id,
        "providerHub": provider_hub,
        "inputHash": {
            "algorithm": "SHA-256",
            "value": compute_hash(input_payload or {"prompt": "Hello"}),
        },
        "outputHash": {
            "algorithm": "SHA-256",
            "value": compute_hash(output_payload or {"response": "Hi there"}),
        },
        "parentReceipts": list(parent_receipts or []),
        "timestamp": timestamp,
    }
    if latency_ms:
        subject["latencyMs"] = latency_ms
    if price_usd:
        subject["priceUsd"] = price_usd
    subject["invocationNonce"] = "legacy-nonce-0001"
    if tee_attestation:
        subject["teeAttestation"] = tee_attestation

    proof_value = signer.sign_canonical(credential_subject_canonical(subject))
    return {
        "@context": list(AWR1_CONTEXT),
        "id": receipt_id or ("urn:uuid:%s" % (uuid.uuid4(),)),
        "type": ["VerifiableCredential", "AIProvenanceReceipt"],
        "issuer": {
            # Appendix D: `did:key:` + the first 32 characters of the base64 public key.
            # Not a DID, and it names no recoverable key — which is why §12.2 takes the
            # key from publicKeyJwk instead.
            "id": provider_hub,
            "name": "Legacy Hub",
            "publicKeyJwk": public_key_to_jwk(signer.public_key_b64),
        },
        "issuanceDate": timestamp,
        "credentialSubject": subject,
        "proof": {
            "type": "Ed25519Signature2018",
            "created": timestamp,
            "verificationMethod": "did:key:%s" % (signer.public_key_b64[:32],),
            "proofPurpose": "assertionMethod",
            "proofValue": proof_value,
        },
        "hubInfo": {
            "hubName": "Legacy Hub",
            "hubVersion": "1.1.0",
            "protocolVersion": "v2",
        },
    }
