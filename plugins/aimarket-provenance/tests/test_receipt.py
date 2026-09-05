"""Tests for ProvenanceReceipt — AWR/2 issuance, signing, verification, serialization."""

from __future__ import annotations

import copy
import json

import pytest

from awr import canonical_sri, parse_did_key, verify_document
from aimarket_provenance.receipt import (
    PAYLOAD_SERIALIZATION_JCS,
    PAYLOAD_SERIALIZATION_JSON,
    PROVENANCE_CONTEXT,
    ProvenanceReceipt,
    compute_hash,
    credential_subject_canonical,
    decimal_string,
    payload_digest,
    public_key_from_jwk,
    public_key_to_jwk,
)


def make(signer, **kwargs) -> ProvenanceReceipt:
    params = dict(
        model_id="claude-sonnet-4@anthropic",
        provider_hub="https://hub.aimarket.org",
        input_payload={"prompt": "Hello"},
        output_payload={"response": "Hi there"},
        signer=signer,
    )
    params.update(kwargs)
    return ProvenanceReceipt.create(**params)


class TestReceiptCreation:
    def test_create_and_sign(self, signer, sample_input, sample_output):
        receipt = make(
            signer,
            input_payload=sample_input,
            output_payload=sample_output,
            hub_name="Test Hub",
            hub_version="1.0.0",
        )
        assert receipt.receipt_id.startswith("urn:uuid:")
        assert receipt.model_id == "claude-sonnet-4@anthropic"
        assert receipt.proof_value
        assert receipt.awr_version == "2.0.0"
        assert receipt.status == "succeeded"
        assert receipt.issuer_public_key_b64 == signer.public_key_b64

    def test_digests_are_sri_not_hex(self, signer):
        """SPEC.md §3.2: a digest reference is an SRI string, not a bare hex digest."""
        receipt = make(signer)
        assert receipt.input_hash.startswith("sha256-")
        assert receipt.output_hash.startswith("sha256-")
        assert receipt.input_hash == receipt.input_digest_sri

    def test_integer_payload_uses_the_spec_canonical_form(self, signer):
        """§3.3 SHOULD: a JSON payload with no float digests over §4 canonical bytes."""
        payload = {"prompt": "Hello", "tokens": 12}
        receipt = make(signer, input_payload=payload, output_payload=payload)
        assert receipt.payload_serialization == PAYLOAD_SERIALIZATION_JCS
        assert receipt.input_hash == canonical_sri(payload)

    def test_float_payload_falls_back_to_a_named_serialization(self, signer):
        """§4.3 forbids a non-integer number, so a payload with 0.7 has no §4 form.

        The receipt then states which rule reproduces its digests rather than leaving a
        reader to try both — the gap is reported as a spec finding.
        """
        payload = {"prompt": "Hello", "temperature": 0.7}
        receipt = make(signer, input_payload=payload, output_payload={"n": 1})
        assert receipt.payload_serialization == PAYLOAD_SERIALIZATION_JSON
        expected, kind = payload_digest(payload)
        assert kind == PAYLOAD_SERIALIZATION_JSON
        assert receipt.input_hash == expected
        # One rule per receipt, so `payloadSerialization` describes both digests: the
        # output digest is the fallback's too, even though this payload has a §4 form.
        assert receipt.output_hash == payload_digest({"n": 1}, PAYLOAD_SERIALIZATION_JSON)[0]

    def test_the_two_payload_rules_are_distinguishable(self, signer):
        """They coincide for most payloads and diverge exactly where §4.3 bites."""
        integers = {"n": 1}
        assert (
            payload_digest(integers, PAYLOAD_SERIALIZATION_JCS)[0]
            == payload_digest(integers, PAYLOAD_SERIALIZATION_JSON)[0]
            == canonical_sri(integers)
        )
        with pytest.raises(Exception):
            # §4 has no canonical form for a non-integer number at all.
            payload_digest({"temperature": 0.7}, PAYLOAD_SERIALIZATION_JCS)

    def test_price_is_a_decimal_string_not_a_json_number(self, signer):
        """§3.3/§4.3: `price.amount` MUST NOT be a JSON number."""
        receipt = make(signer, price_usd=0.15)
        price = receipt.to_dict()["credentialSubject"]["price"]
        assert price == {"currency": "USD", "amount": "0.15"}
        assert isinstance(price["amount"], str)
        assert receipt.price_amount == "0.15"
        assert receipt.price_usd == pytest.approx(0.15)

    def test_no_non_integer_number_anywhere_in_the_document(self, signer):
        """§4.3 is a rule about the whole signed document, not only about `price`."""
        receipt = make(signer, price_usd=0.15, reputation_score=0.87, latency_ms=2340)

        def walk(value, path="$"):
            if isinstance(value, bool):
                return
            if isinstance(value, float):
                pytest.fail("non-integer JSON number at %s: %r" % (path, value))
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(item, "%s.%s" % (path, key))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, "%s[%d]" % (path, index))

        walk(receipt.to_dict())
        assert receipt.to_dict()["credentialSubject"]["reputationScore"] == "0.87"
        assert receipt.to_dict()["credentialSubject"]["work"]["latencyMs"] == 2340

    def test_started_at_precedes_completed_at(self, signer):
        receipt = make(signer, latency_ms=2340)
        work = receipt.to_dict()["credentialSubject"]["work"]
        assert work["startedAt"] <= work["completedAt"]
        assert receipt.latency_ms == 2340

    def test_create_with_parent_receipts(self, signer):
        first = make(signer)
        second = make(signer)
        child = make(signer, parent_receipts=[first, second])
        assert len(child.parents) == 2
        assert child.parent_receipts == [first.receipt_id, second.receipt_id]
        for parent, edge in zip((first, second), child.parents):
            assert edge["digestSRI"] == parent.digest_sri

    def test_parent_identifier_alone_is_refused(self, signer):
        """§13.1: an id-only edge is what let AWR/1 chains be re-pointed."""
        with pytest.raises(ValueError, match="digest reference"):
            make(signer, parent_receipts=["urn:uuid:parent-001"])

    def test_create_with_tee_attestation(self, signer):
        tee = {
            "platform": "AWS_NITRO",
            "enclaveId": "i-test",
            "codeHash": "sha256:abc",
            "timestamp": "2026-05-23T12:00:00Z",
            "signature": "sig_test",
        }
        receipt = make(signer, tee_attestation=tee)
        assert receipt.tee_attestation == tee
        # §7.2: inside `environment`, therefore inside the AWR signature.
        assert receipt.to_dict()["credentialSubject"]["environment"]["teeAttestation"] == tee

    def test_zk_proofs_land_under_the_spec_named_member(self, signer):
        receipt = make(
            signer,
            zk_input_proof={"scheme": "groth16", "proof": "0xaa"},
            zk_output_proof={"scheme": "groth16", "proof": "0xbb"},
        )
        env = receipt.to_dict()["credentialSubject"]["environment"]
        assert env["zkProof"] == {
            "input": {"scheme": "groth16", "proof": "0xaa"},
            "output": {"scheme": "groth16", "proof": "0xbb"},
        }
        assert receipt.zk_input_proof == {"scheme": "groth16", "proof": "0xaa"}

    def test_failed_work_is_a_first_class_receipt(self, signer):
        """§3.3: a receipt for work that did not succeed is a first-class document."""
        receipt = make(signer, status="failed", output_payload={})
        assert receipt.status == "failed"
        assert receipt.verify() is True

    def test_unknown_status_is_refused(self, signer):
        with pytest.raises(ValueError, match="work.status"):
            make(signer, status="probably-fine")

    def test_awr1_is_not_issuable(self, signer):
        """§12: an implementation supporting AWR/1 MUST NOT issue AWR/1."""
        receipt = make(signer)
        assert receipt.is_legacy is False
        assert receipt.proof_type == "DataIntegrityProof"
        assert receipt.cryptosuite == "eddsa-jcs-2022"
        # There is no parameter that produces the legacy proof; the capability is absent.
        with pytest.raises(TypeError):
            ProvenanceReceipt.create(  # type: ignore[call-arg]
                model_id="m",
                provider_hub="p",
                input_payload={},
                output_payload={},
                signer=signer,
                proof_type="Ed25519Signature2018",
            )


class TestDidKey:
    def test_issuer_is_a_real_did_key_round_trip(self, signer):
        """§5.1: the DID *is* the key — decoding it returns the hub's public key.

        AWR/1 wrote `did:key:` + the first 32 characters of the base64 key, which decodes
        to nothing and names no key (Appendix D).
        """
        receipt = make(signer)
        did = receipt.issuer_id
        assert did.startswith("did:key:z6Mk")
        assert len(did[len("did:key:"):]) == 48

        import base64

        assert parse_did_key(did) == base64.b64decode(signer.public_key_b64)
        # And the round trip the other way: the same key derives the same DID.
        from awr import derive_did_key

        assert derive_did_key(base64.b64decode(signer.public_key_b64)) == did

    def test_verification_method_is_the_did_plus_its_own_fragment(self, signer):
        """§5.3: `<issuer.id>#<method-specific-id>`, so a verifier never chooses a key."""
        receipt = make(signer)
        did = receipt.issuer_id
        assert receipt.proof_verification_method == "%s#%s" % (did, did[len("did:key:"):])

    def test_a_forged_key_announces_itself_as_a_different_issuer(self, signer, other_signer):
        """The AWR/1 attack: swap the embedded key and the receipt still names our hub."""
        ours = make(signer)
        theirs = make(other_signer)
        assert ours.issuer_id != theirs.issuer_id
        # Under AWR/2 there is no separate embedded key to swap: it is derived from the
        # DID, so a forgery cannot present our identity while signing with its own key.
        assert theirs.issuer_public_key_b64 == other_signer.public_key_b64
        assert theirs.verify() is True  # validly signed — by someone else (§13.7)


class TestReceiptVerification:
    def test_verify_valid_receipt(self, signer, sample_input, sample_output):
        receipt = make(signer, input_payload=sample_input, output_payload=sample_output)
        assert receipt.verify() is True
        result = receipt.verify_result()
        assert result["valid"] is True
        assert result["awrVersion"] == "2.0.0"
        assert result["documentType"] == "WorkReceipt"
        assert result["profile"] == "L0"
        assert result["verifiedProof"] == 0

    def test_verify_tampered_output_digest(self, signer):
        receipt = make(signer)
        receipt.document["credentialSubject"]["outputDigest"] = (
            "sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="
        )
        assert receipt.verify() is False
        assert "AWR-PROOF-006" in [r["code"] for r in receipt.verify_result()["reasons"]]

    def test_verify_tampered_id(self, signer):
        """The attack AWR/2 exists to close: `id` is inside the signature (§13.1).

        In AWR/1 the signature covered `credentialSubject` only, so renaming a receipt
        broke nothing and a chain could be re-pointed at the renamed document.
        """
        receipt = make(signer)
        original = receipt.receipt_id
        receipt.document["id"] = "urn:uuid:00000000-0000-4000-8000-000000000000"
        assert receipt.receipt_id != original
        assert receipt.verify() is False
        assert [r["code"] for r in receipt.verify_result()["reasons"]] == ["AWR-PROOF-006"]

    def test_verify_tampered_issuer(self, signer, other_signer):
        """`issuer` is inside the signature too, so it cannot be rewritten."""
        receipt = make(signer)
        stolen = make(other_signer).issuer_id
        receipt.document["issuer"]["id"] = stolen
        result = receipt.verify_result()
        assert result["valid"] is False
        assert "AWR-PROOF-006" in [r["code"] for r in result["reasons"]]

    def test_verify_tampered_type(self, signer):
        """`type` is inside the signature; in AWR/1 it was not."""
        receipt = make(signer)
        receipt.document["type"] = ["VerifiableCredential", "VerificationVerdict"]
        assert receipt.verify() is False

    def test_verify_tampered_hub_info(self, signer):
        """`hubInfo` sat outside the AWR/1 signature; here it is a signed top-level member."""
        receipt = make(signer, hub_name="Test Hub", hub_version="3.0.0")
        receipt.document["hubInfo"]["hubName"] = "Someone Else's Hub"
        assert receipt.verify() is False

    def test_verify_tampered_signature(self, signer):
        receipt = make(signer)
        receipt.document["proof"]["proofValue"] = "tampered_signature"
        result = receipt.verify_result()
        assert result["valid"] is False
        # §6.3: a proofValue that does not decode is AWR-PROOF-005, and the verifier MUST
        # NOT also report AWR-PROOF-006 — the step that prevented the check is more
        # specific.
        codes = [r["code"] for r in result["reasons"]]
        assert "AWR-PROOF-005" in codes
        assert "AWR-PROOF-006" not in codes
        assert result["verifiedProof"] is None

    def test_verify_no_signature(self, signer):
        receipt = make(signer)
        del receipt.document["proof"]
        assert receipt.verify() is False
        assert "AWR-PROOF-001" in [r["code"] for r in receipt.verify_result()["reasons"]]

    def test_legacy_base64_proof_value_is_rejected_in_awr2(self, signer):
        """§6.1: the legacy base64 form MUST be rejected in an AWR/2 document."""
        receipt = make(signer)
        receipt.document["proof"]["proofValue"] = (
            "D2diPTmadpH0YM7/MqJRM78OObMN6pcYZJxjxGtXc/3dGIeEYZ8jQ28VV2mNc59uSWuhi4p241Ky1ahRpNvECg=="
        )
        assert "AWR-PROOF-005" in [
            r["code"] for r in receipt.verify_result()["reasons"]
        ]

    def test_attestation_is_warned_about_not_verified(self, signer):
        """§7.3: AWR-ENV-001 is the correct and honest outcome, at warning severity."""
        receipt = make(signer, tee_attestation={"platform": "AWS_NITRO"})
        result = receipt.verify_result()
        assert result["valid"] is True
        assert "AWR-ENV-001" in [w["code"] for w in result["warnings"]]
        assert "AWR-ENV-001" not in [r["code"] for r in result["reasons"]]


class TestSerialization:
    def test_round_trip_to_dict(self, signer, sample_input, sample_output):
        receipt = make(signer, input_payload=sample_input, output_payload=sample_output)
        r2 = ProvenanceReceipt.from_dict(receipt.to_dict())
        assert r2.model_id == receipt.model_id
        assert r2.input_hash == receipt.input_hash
        assert r2.output_hash == receipt.output_hash
        assert r2.proof_value == receipt.proof_value
        assert r2.verify() is True

    def test_round_trip_via_json(self, signer, sample_input, sample_output):
        receipt = make(signer, input_payload=sample_input, output_payload=sample_output)
        r2 = ProvenanceReceipt.from_json(json.dumps(receipt.to_dict()))
        assert r2.verify() is True
        # §4.2: verification runs over the received bytes, not a re-serialization.
        assert r2.source_bytes is not None

    def test_to_dict_is_a_copy(self, signer):
        receipt = make(signer)
        out = receipt.to_dict()
        out["id"] = "urn:uuid:mutated"
        assert receipt.receipt_id != "urn:uuid:mutated"
        assert receipt.verify() is True

    def test_w3c_vc_structure(self, signer):
        receipt = make(signer, hub_name="Test Hub")
        d = receipt.to_dict()
        assert d["@context"] == PROVENANCE_CONTEXT
        assert d["@context"][0] == "https://www.w3.org/ns/credentials/v2"
        assert "https://verify.modelmarket.dev/ns/awr/v2" in d["@context"]
        assert d["id"].startswith("urn:uuid:")
        assert "VerifiableCredential" in d["type"]
        assert "WorkReceipt" in d["type"]
        assert "AIProvenanceReceipt" in d["type"]
        assert d["awrVersion"] == "2.0.0"
        assert isinstance(d["issuer"], dict) and d["issuer"]["id"].startswith("did:key:")
        assert d["validFrom"].endswith("Z")
        assert "credentialSubject" in d
        assert d["proof"]["type"] == "DataIntegrityProof"
        assert d["proof"]["cryptosuite"] == "eddsa-jcs-2022"
        assert d["proof"]["proofPurpose"] == "assertionMethod"
        assert d["proof"]["proofValue"].startswith("z")

    def test_reference_implementation_agrees(self, signer):
        """The document verifies through `awr` itself, not only through the plugin."""
        receipt = make(signer)
        result = verify_document(receipt.to_dict())
        assert result["valid"] is True, result["reasons"]
        assert result["profile"] == "L0"

    def test_jwk_round_trip(self, signer):
        b64 = signer.public_key_b64
        jwk = public_key_to_jwk(b64)
        assert jwk["kty"] == "OKP"
        assert jwk["crv"] == "Ed25519"
        assert jwk["x"]
        assert public_key_from_jwk(jwk) == b64

    def test_digest_reference_is_over_the_secured_document(self, signer):
        """§8.1: the edge digests the secured parent, `proof` included."""
        receipt = make(signer)
        reference = receipt.digest_reference()
        assert reference["id"] == receipt.receipt_id
        assert reference["digestSRI"] == canonical_sri(receipt.to_dict())
        unsecured = copy.deepcopy(receipt.to_dict())
        del unsecured["proof"]
        assert reference["digestSRI"] != canonical_sri(unsecured)


class TestHelpers:
    def test_decimal_string(self):
        assert decimal_string(0.15) == "0.15"
        assert decimal_string(0) == "0"
        assert decimal_string(5) == "5"
        assert decimal_string("0.10") == "0.10"
        with pytest.raises(ValueError):
            decimal_string(float("nan"))
        with pytest.raises(ValueError):
            decimal_string("not a number")

    def test_payload_digest_is_deterministic(self):
        payload = {"a": 1, "b": 2}
        assert payload_digest(payload) == payload_digest({"b": 2, "a": 1})
        assert payload_digest(payload)[0] != payload_digest({"a": 2})[0]

    def test_legacy_hash_helpers_still_available(self):
        """Kept so an AWR/1 row's `input_hash` can still be reproduced from its payload."""
        h1 = compute_hash({"a": 1, "b": 2})
        assert h1 == compute_hash({"b": 2, "a": 1})
        assert len(h1) == 64
        c1 = credential_subject_canonical({"modelId": "x", "providerHub": "y"})
        assert c1 == credential_subject_canonical({"providerHub": "y", "modelId": "x"})
