"""AWR/1 legacy verification for stored receipts (``awr/SPEC.md`` §12).

Two things are asserted here, and the second is why this file exists:

1. a receipt this plugin signed **before** the AWR/2 migration still verifies, is reported
   as legacy, never claims a profile, and never presents its unsigned fields as attested;
2. the reference implementation's §12.1 renderer **cannot** verify it — the pipe-delimited
   layout §12.1 writes out is not the layout the AWR/1 issuer in this repository actually
   produced.  That divergence is the reason ``aimarket_provenance.legacy`` keeps its own
   rendering instead of delegating, and it is reported upstream as a spec finding.
"""

from __future__ import annotations

import json

import pytest

from awr import verify as awr_verify
from awr.legacy import legacy_canonical_form
from aimarket_provenance.legacy import (
    DIALECT_NATIVE,
    credential_subject_canonical,
    is_legacy_document,
    verify_legacy_receipt,
)
from aimarket_provenance.receipt import ProvenanceReceipt
from aimarket_provenance.verifier import verify_receipt

from .conftest import build_awr1_document


@pytest.fixture
def legacy_document(signer) -> dict:
    return build_awr1_document(signer)


def _signer_for_seed(tmp_path, seed: int):
    """A ``Signer`` over a chosen Ed25519 seed, in the 64-byte ``seed || pub`` key file."""
    from awr import SigningKey

    from aimarket_hub.signing import Signer

    raw = seed.to_bytes(32, "big")
    path = tmp_path / ("seed-%d" % (seed,))
    path.write_bytes(raw + SigningKey.from_seed(raw).public_key_bytes)
    return Signer(key_path=str(path))


class TestLegacyDialectDivergence:
    def test_native_and_spec_renderings_differ(self, legacy_document):
        """§12.1 describes ``path=leaf``; the AWR/1 issuer here wrote ``key:value``."""
        subject = legacy_document["credentialSubject"]
        native = credential_subject_canonical(subject)
        dialect_a = legacy_canonical_form(subject, "A").decode()
        assert native != dialect_a
        # The separator and the flattening both differ, not just the number rendering.
        assert "modelId:claude-sonnet-4@anthropic" in native
        assert "modelId=claude-sonnet-4@anthropic" in dialect_a
        assert "inputHash.value=" in dialect_a
        assert "inputHash.value" not in native

    def test_the_reference_implementation_alone_cannot_verify_a_stored_receipt(
        self, legacy_document
    ):
        """This is the finding: `awr` reports AWR-LEGACY-002 for a valid stored receipt."""
        result = awr_verify(legacy_document)
        assert result["valid"] is False
        assert [r["code"] for r in result["reasons"]] == ["AWR-LEGACY-002"]

    def test_the_plugin_verifies_it_by_trying_its_own_dialect_first(self, legacy_document):
        result = verify_legacy_receipt(legacy_document)
        assert result["valid"] is True, result["reasons"]
        assert result["legacyDialect"] == DIALECT_NATIVE

    def test_a_spec_dialect_document_still_verifies(self, signer):
        """§12 requires an AWR/1-supporting verifier to try both §12.1 dialects."""
        subject = {
            "work": {"modelId": "legacy@vendor", "latencyMs": 2340},
            "inputDigest": "sha256-ntYicspG8WqhUyawUlC4dTFMnG08+B5Wol6Kci8rnNo=",
        }
        for dialect in ("A", "B"):
            document = {
                "@context": ["https://www.w3.org/2018/credentials/v1"],
                "id": "urn:uuid:00000041-1e6a-4c11-8f00-00000000000%s" % (dialect,),
                "type": ["VerifiableCredential", "WorkReceipt"],
                "issuer": {
                    "id": "did:key:%s" % (signer.public_key_b64[:32],),
                    "publicKeyJwk": {
                        "kty": "OKP",
                        "crv": "Ed25519",
                        "x": __import__("base64")
                        .urlsafe_b64encode(
                            __import__("base64").b64decode(signer.public_key_b64)
                        )
                        .rstrip(b"=")
                        .decode(),
                    },
                },
                "credentialSubject": subject,
                "proof": {
                    "type": "Ed25519Signature2018",
                    "created": "2026-01-15T09:00:05Z",
                    "verificationMethod": "did:key:x#key-1",
                    "proofPurpose": "assertionMethod",
                    "proofValue": __import__("base64")
                    .b64encode(signer.sign(legacy_canonical_form(subject, dialect)))
                    .decode(),
                },
            }
            result = verify_legacy_receipt(document)
            assert result["valid"] is True, (dialect, result["reasons"])
            assert result["legacyDialect"] == dialect


class TestLegacyVerification:
    def test_detected_as_legacy(self, legacy_document):
        assert is_legacy_document(legacy_document) is True
        receipt = ProvenanceReceipt.from_dict(legacy_document)
        assert receipt.is_legacy is True
        assert receipt.awr_version is None  # §11.1: AWR/1 carries no awrVersion

    def test_legacy_warning_is_always_reported(self, legacy_document):
        result = verify_legacy_receipt(legacy_document)
        assert "AWR-LEGACY-001" in [w["code"] for w in result["warnings"]]
        # §11.1: exactly one severity per code — a warning never appears in `reasons`.
        assert "AWR-LEGACY-001" not in [r["code"] for r in result["reasons"]]

    def test_unsigned_fields_are_named(self, legacy_document):
        """§12/§13.1: id, type, issuer and hubInfo are NOT attested by an AWR/1 proof."""
        result = verify_legacy_receipt(legacy_document)
        assert set(result["unsignedFields"]) == {
            "id",
            "type",
            "issuer",
            "hubInfo",
            "issuanceDate",
        }

    def test_no_profile_for_a_legacy_document(self, legacy_document):
        """§10 profiles are defined over AWR/2 documents; §11.1 wants `null`, not "below L0"."""
        result = verify_legacy_receipt(legacy_document)
        assert result["profile"] is None
        assert result["verifiedProof"] is None  # an AWR/1 signature is not a §6.1 proof

    def test_renaming_a_legacy_receipt_still_verifies(self, legacy_document):
        """The defect itself, kept visible: AWR/1 leaves `id` outside the signature.

        The receipt below has been renamed by "an intermediary" and its AWR/1 signature is
        untouched — which is exactly why `unsignedFields` has to be reported and why AWR/2
        signs the whole document (§13.1).
        """
        renamed = json.loads(json.dumps(legacy_document))
        renamed["id"] = "urn:uuid:00000000-0000-4000-8000-000000000000"
        result = verify_legacy_receipt(renamed)
        assert result["valid"] is True
        assert "id" in result["unsignedFields"]

    def test_tampering_the_subject_does_break_it(self, legacy_document):
        legacy_document["credentialSubject"]["modelId"] = "something-else"
        result = verify_legacy_receipt(legacy_document)
        assert result["valid"] is False
        assert "AWR-LEGACY-002" in [r["code"] for r in result["reasons"]]

    def test_multibase_proof_value_in_a_legacy_document(self, legacy_document, signer):
        """§12.2: a proofValue that is not base64 is AWR-PROOF-005, not AWR-LEGACY-002."""
        current = ProvenanceReceipt.create(
            model_id="m@p",
            provider_hub="p",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=signer,
        )
        # A genuine AWR/2 proofValue: multibase base58btc of 64 bytes (§6.1).
        legacy_document["proof"]["proofValue"] = current.proof_value
        result = verify_legacy_receipt(legacy_document)
        assert [r["code"] for r in result["reasons"]] == ["AWR-PROOF-005"]

    def test_a_z_prefixed_base64_signature_still_verifies(self, tmp_path):
        """One AWR/1 signature in ~64 starts with 'z', and 'z' is also the multibase tag.

        A verifier that reads the prefix before attempting to decode therefore rejects
        about 1.6% of all AWR/1 receipts ever issued, telling their owners they are AWR/2
        documents. The signature below is a real one, found by searching seeds until the
        base64 encoding began with 'z'.
        """
        from awr import SigningKey

        from aimarket_hub.signing import Signer
        from aimarket_provenance.legacy import credential_subject_canonical

        probe = build_awr1_document(_signer_for_seed(tmp_path, 0))
        message = credential_subject_canonical(probe["credentialSubject"]).encode()

        import base64 as _b64

        for seed in range(1, 4096):
            key = SigningKey.from_seed(seed.to_bytes(32, "big"))
            if _b64.b64encode(key.sign(message)).decode().startswith("z"):
                break
        else:  # pragma: no cover - 4096 tries fail with probability (63/64)^4095
            pytest.skip("no z-prefixed signature found in 4096 seeds")

        signer: Signer = _signer_for_seed(tmp_path, seed)
        document = build_awr1_document(signer)
        assert document["proof"]["proofValue"].startswith("z")

        result = verify_legacy_receipt(document)
        assert result["valid"] is True, result["reasons"]
        assert result["legacyDialect"] == DIALECT_NATIVE

    def test_no_usable_key_is_awr_key_001(self, legacy_document):
        """§12.2: `did:key:` + 32 base64 characters names no key, so the JWK is all there is."""
        del legacy_document["issuer"]["publicKeyJwk"]
        result = verify_legacy_receipt(legacy_document)
        assert [r["code"] for r in result["reasons"]] == ["AWR-KEY-001"]

    def test_awr2_rules_are_not_applied_to_a_legacy_document(self, legacy_document):
        """§12: applying §3.1, §3.3, §4.3 or §5.1 would fail every AWR/1 document.

        The stored receipt carries a `priceUsd` JSON float and a VC 1.1 context, both of
        which AWR/2 forbids — and neither is reported, because they are not AWR/1 rules.
        """
        assert isinstance(legacy_document["credentialSubject"]["priceUsd"], float)
        result = verify_legacy_receipt(legacy_document)
        assert result["valid"] is True
        reported = {r["code"] for r in result["reasons"]}
        assert not any(code.startswith("AWR-CANON-") for code in reported)
        assert not any(code.startswith("AWR-DOC-") for code in reported)
        assert not any(code.startswith("AWR-RCPT-") for code in reported)


class TestLegacyThroughThePluginSurface:
    def test_receipt_wrapper_reads_awr1_field_names(self, signer, legacy_document):
        receipt = ProvenanceReceipt.from_dict(legacy_document)
        assert receipt.model_id == "claude-sonnet-4@anthropic"
        assert receipt.provider_hub == "https://hub.aimarket.org"
        assert len(receipt.input_hash) == 64  # AWR/1 carried bare hex, not SRI
        assert receipt.latency_ms == 2340
        assert receipt.price_amount == "0.15"
        assert receipt.invocation_nonce == "legacy-nonce-0001"
        assert receipt.issuer_public_key_b64 == signer.public_key_b64
        assert receipt.verify() is True

    def test_verifier_reports_a_legacy_receipt_as_legacy(self, legacy_document):
        receipt = ProvenanceReceipt.from_dict(legacy_document)
        result = verify_receipt(receipt)
        assert result.valid is True
        signature = next(c for c in result.checks if c["check"] == "signature")
        assert signature["legacy"] is True
        assert signature["legacy_dialect"] == DIALECT_NATIVE
        assert "id" in signature["unsigned_fields"]
        assert any("AWR-LEGACY-001" in w for w in result.warnings)

    def test_legacy_receipt_survives_a_bytes_round_trip(self, legacy_document):
        """§12: the strict §4.3 parser must re-read a legacy document leniently.

        `priceUsd` is a JSON float — legal in AWR/1, AWR-CANON-001 in AWR/2. Without the
        second pass every stored receipt with a price would be unreadable.
        """
        raw = json.dumps(legacy_document, indent=2)
        assert '"priceUsd": 0.15' in raw
        receipt = ProvenanceReceipt.from_json(raw)
        assert receipt.is_legacy is True
        assert receipt.verify() is True

    def test_the_lenient_re_read_is_only_for_legacy_documents(self, signer):
        """An AWR/2 document with a non-integer number stays AWR-CANON-001 (§4.3)."""
        current = ProvenanceReceipt.create(
            model_id="m@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=signer,
        )
        document = current.to_dict()
        document["credentialSubject"]["reputationScore"] = 0.87  # a float, not a string
        with pytest.raises(Exception) as caught:
            ProvenanceReceipt.from_json(json.dumps(document))
        assert getattr(caught.value, "code", "") == "AWR-CANON-001"

    def test_legacy_chain_edges_are_identifiers_only(self, signer):
        """AWR/1 `parentReceipts` are ids, hence re-pointable (§8.1 vs §13.1)."""
        document = build_awr1_document(
            signer, parent_receipts=["urn:uuid:parent-1", "urn:uuid:parent-2"]
        )
        receipt = ProvenanceReceipt.from_dict(document)
        assert receipt.parent_receipts == ["urn:uuid:parent-1", "urn:uuid:parent-2"]
        assert receipt.parents == []  # no digest references exist to report
        result = verify_receipt(receipt)
        assert result.valid is True
        edges = next(c for c in result.checks if c["check"] == "parent_receipts")
        assert edges["content_addressed"] is False

    def test_a_legacy_tee_attestation_is_still_only_warned_about(self, signer):
        document = build_awr1_document(
            signer, tee_attestation={"platform": "AWS_NITRO", "signature": "sig"}
        )
        receipt = ProvenanceReceipt.from_dict(document)
        result = verify_receipt(receipt)
        assert result.valid is True
        check = next(c for c in result.checks if c["check"] == "tee_attestation")
        assert check["verified"] is False
