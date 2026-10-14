"""Tests for the standalone verifier logic."""

from __future__ import annotations

import datetime as _dt

import pytest

from aimarket_provenance.receipt import ProvenanceReceipt
from aimarket_provenance.verifier import VerificationResult, verify_receipt


@pytest.fixture
def valid_receipt(signer) -> ProvenanceReceipt:
    return ProvenanceReceipt.create(
        model_id="claude-sonnet-4@anthropic",
        provider_hub="https://hub.aimarket.org",
        input_payload={"prompt": "Hello"},
        output_payload={"response": "Hi there"},
        signer=signer,
    )


def codes(result: VerificationResult) -> list[str]:
    return [r["code"] for r in result.awr.get("reasons", [])]


def warning_codes(result: VerificationResult) -> list[str]:
    return [w["code"] for w in result.awr.get("warnings", [])]


class TestVerifier:
    def test_verify_valid(self, valid_receipt):
        result = verify_receipt(valid_receipt)
        assert result.valid is True
        assert result.errors == []
        assert result.awr["profile"] == "L0"
        assert result.awr["verifiedProof"] == 0

    def test_verify_tampered_signature(self, valid_receipt):
        valid_receipt.document["proof"]["proofValue"] = "tampered"
        result = verify_receipt(valid_receipt)
        assert result.valid is False
        assert any("AWR-PROOF-005" in e for e in result.errors)
        signature = next(c for c in result.checks if c["check"] == "signature")
        assert signature["passed"] is False
        assert signature["verified_proof"] is None

    def test_verify_tampered_id_is_now_caught(self, valid_receipt):
        """The AWR/1 hole: `id` was unsigned, so a receipt could be renamed at will."""
        valid_receipt.document["id"] = "urn:uuid:11111111-2222-4333-8444-555555555555"
        result = verify_receipt(valid_receipt)
        assert result.valid is False
        assert codes(result) == ["AWR-PROOF-006"]
        assert next(c for c in result.checks if c["check"] == "signature")["passed"] is False

    def test_verify_missing_model_id(self, valid_receipt):
        valid_receipt.document["credentialSubject"]["work"]["modelId"] = ""
        result = verify_receipt(valid_receipt)
        assert result.valid is False
        # AWR-RCPT-005 names the field; AWR-PROOF-006 says the bytes no longer match.
        # §11.1 requires every error the verifier can determine, not only the first.
        assert "AWR-RCPT-005" in codes(result)
        assert "AWR-PROOF-006" in codes(result)
        structure = next(c for c in result.checks if c["check"] == "structure")
        assert structure["passed"] is False
        assert "AWR-RCPT-005" in structure["codes"]

    def test_future_timestamp_is_a_warning_not_a_failure(self, signer):
        """§11.3: age is policy, not validity — and so is a clock ahead of the reader's.

        The pre-migration verifier hard-failed both a future timestamp and anything older
        than 90 days (Appendix D). A legitimately issued receipt read by a verifier whose
        clock is behind is still cryptographically sound.
        """
        future = _dt.datetime(2099, 1, 1, tzinfo=_dt.timezone.utc)
        receipt = ProvenanceReceipt.create(
            model_id="m@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=signer,
            now=future,
        )
        result = verify_receipt(receipt)
        assert result.valid is True
        assert "AWR-TIME-001" in warning_codes(result)
        timestamp = next(c for c in result.checks if c["check"] == "timestamp")
        assert timestamp["passed"] is True
        assert timestamp["codes"] == ["AWR-TIME-001"]

    def test_tampering_the_timestamp_breaks_the_signature(self, valid_receipt):
        """`validFrom` is inside the signed bytes, so it cannot be moved."""
        valid_receipt.document["validFrom"] = "2099-01-01T00:00:00Z"
        result = verify_receipt(valid_receipt)
        assert result.valid is False
        assert "AWR-PROOF-006" in codes(result)

    def test_age_policy_is_opt_in(self, signer):
        old = _dt.datetime(2020, 1, 1, tzinfo=_dt.timezone.utc)
        receipt = ProvenanceReceipt.create(
            model_id="m@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=signer,
            now=old,
        )
        # Default: no age check at all (§11.3).
        assert verify_receipt(receipt).valid is True
        # A caller may still apply its own policy, and it is reported as policy.
        strict = verify_receipt(receipt, max_age_seconds=86400)
        assert strict.valid is False
        assert any("policy" in e for e in strict.errors)

    def test_invalid_digest_format(self, valid_receipt):
        valid_receipt.document["credentialSubject"]["inputDigest"] = "short"
        result = verify_receipt(valid_receipt)
        assert result.valid is False
        assert "AWR-RCPT-001" in codes(result)
        assert next(c for c in result.checks if c["check"] == "hash_format")["passed"] is False

    def test_parent_edge_resolution(self, signer):
        parent = ProvenanceReceipt.create(
            model_id="m1@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=signer,
        )
        child = ProvenanceReceipt.create(
            model_id="m2@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"b": 2},
            output_payload={"c": 3},
            signer=signer,
            parent_receipts=[parent],
        )
        resolved = verify_receipt(child, parents=[parent])
        assert resolved.valid is True
        assert resolved.awr["chain"] == {"resolved": 1, "unresolved": 0}

        # §8.2: an edge whose parent was not supplied is `unresolved`, NOT an error — a
        # verifier MUST NOT fetch it (§13.5), and "chain not checked" is not "chain broken".
        alone = verify_receipt(child)
        assert alone.valid is True
        assert alone.awr["chain"] == {"resolved": 0, "unresolved": 1}
        edges = next(c for c in alone.checks if c["check"] == "parent_receipts")
        assert edges["count"] == 1
        assert edges["unresolved"] == 1
        assert edges["content_addressed"] is True

    def test_a_substituted_parent_is_detected(self, signer):
        """§8.1: the edge commits to the parent's bytes, so a swap is caught."""
        parent = ProvenanceReceipt.create(
            model_id="m1@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=signer,
        )
        child = ProvenanceReceipt.create(
            model_id="m2@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"b": 2},
            output_payload={"c": 3},
            signer=signer,
            parent_receipts=[parent],
        )
        # An impostor that took the parent's identifier — the AWR/1 re-pointing attack.
        impostor = ProvenanceReceipt.create(
            model_id="evil@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 9},
            output_payload={"b": 9},
            signer=signer,
            receipt_id=parent.receipt_id,
        )
        result = verify_receipt(child, parents=[impostor])
        assert result.valid is False
        assert "AWR-CHAIN-003" in codes(result)

    def test_issuer_binding_pins_the_did_key(self, valid_receipt, other_signer):
        did = valid_receipt.issuer_id
        bound = verify_receipt(
            valid_receipt, trusted_issuer_keys={did: valid_receipt.issuer_public_key_b64}
        )
        assert bound.valid is True
        check = next(c for c in bound.checks if c["check"] == "issuer_binding")
        assert check["bound"] is True
        assert check["pinned_as"] == did

        wrong = verify_receipt(
            valid_receipt, trusted_issuer_keys={did: other_signer.public_key_b64}
        )
        assert wrong.valid is False
        assert any("Issuer key mismatch" in e for e in wrong.errors)

    def test_issuer_binding_accepts_a_did_as_the_pinned_value(self, valid_receipt):
        did = valid_receipt.issuer_id
        result = verify_receipt(valid_receipt, trusted_issuer_keys={did: did})
        assert result.valid is True
        assert next(c for c in result.checks if c["check"] == "issuer_binding")["bound"]

    def test_unpinned_issuer_is_reported_as_unbound_not_invalid(self, valid_receipt):
        """§13.7: a valid document means the issuer signed it, and nothing more."""
        result = verify_receipt(valid_receipt)
        check = next(c for c in result.checks if c["check"] == "issuer_binding")
        assert check["passed"] is True
        assert check["bound"] is False
        assert check["did_key"] == valid_receipt.issuer_id

    def test_a_forged_receipt_fails_the_pin(self, signer, other_signer):
        """The forgery AWR/1 permitted: sign with your own key, claim to be the hub."""
        forged = ProvenanceReceipt.create(
            model_id="m@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=other_signer,
        )
        # Validly signed — by someone else. Only the pin distinguishes the two.
        assert forged.verify() is True
        result = verify_receipt(
            forged,
            trusted_issuer_keys={"https://hub.aimarket.org": signer.public_key_b64},
        )
        assert result.valid is False
        assert any("Issuer key mismatch" in e for e in result.errors)

    def test_attestation_is_never_claimed_as_verified(self, signer):
        """§7.3: the pre-migration verifier checked a TEE signature with the receipt

        issuer's own key, which proves only that the claimant wrote it down while
        presenting as hardware evidence.
        """
        receipt = ProvenanceReceipt.create(
            model_id="m@p",
            provider_hub="https://hub.aimarket.org",
            input_payload={"a": 1},
            output_payload={"b": 2},
            signer=signer,
            tee_attestation={
                "platform": "AWS_NITRO",
                "codeHash": "sha256:abc",
                "signature": "sig",
            },
        )
        result = verify_receipt(receipt)
        assert result.valid is True
        check = next(c for c in result.checks if c["check"] == "tee_attestation")
        assert check["verified"] is False
        assert check["codes"] == ["AWR-ENV-001"]
        assert "AWR-ENV-001" in warning_codes(result)

    def test_profile_codes_only_for_a_requested_profile(self, valid_receipt):
        """§10.4: reporting AWR-PROFILE-* unrequested would invalidate every L0 receipt."""
        assert verify_receipt(valid_receipt).valid is True
        requested = verify_receipt(valid_receipt, profile="L1")
        assert requested.valid is False
        assert "AWR-PROFILE-001" in codes(requested)
        assert requested.awr["profile"] is None

    def test_checks_list_populated(self, valid_receipt):
        result = verify_receipt(valid_receipt)
        names = [c["check"] for c in result.checks]
        assert "structure" in names
        assert "signature" in names
        assert "issuer_binding" in names
        assert "timestamp" in names
        assert "hash_format" in names

    def test_to_dict(self, valid_receipt):
        d = verify_receipt(valid_receipt).to_dict()
        assert d["valid"] is True
        assert isinstance(d["checks"], list)
        assert isinstance(d["errors"], list)
        assert isinstance(d["warnings"], list)
        # The verbatim §11.1 result, which is what another implementation is compared to.
        assert d["awr"]["awrVersion"] == "2.0.0"
        assert set(d["awr"]) >= {
            "valid",
            "awrVersion",
            "documentType",
            "profile",
            "reasons",
            "warnings",
            "chain",
            "verifiedProof",
        }

    def test_signer_argument_is_ignored(self, valid_receipt, other_signer):
        """§5.1: the key comes from `issuer.id`; a verifier never chooses one."""
        assert verify_receipt(valid_receipt, other_signer).valid is True
        assert valid_receipt.verify(other_signer) is True
