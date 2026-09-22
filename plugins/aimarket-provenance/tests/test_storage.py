"""Tests for ProvenanceStorage — receipt persistence across the AWR/1 → AWR/2 change."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from aimarket_provenance.receipt import ProvenanceReceipt
from aimarket_provenance.storage import ProvenanceStorage

from .conftest import build_awr1_document


@pytest.fixture
def storage() -> ProvenanceStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = ProvenanceStorage(db_path=str(Path(tmp) / "test_provenance.db"))
        yield s
        s.close()


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


@pytest.fixture
def sample_receipt(signer) -> ProvenanceReceipt:
    return make(signer)


class TestStorage:
    def test_store_and_retrieve(self, storage, sample_receipt):
        storage.store(sample_receipt)
        retrieved = storage.get_by_receipt_id(sample_receipt.receipt_id)
        assert retrieved is not None
        assert retrieved.model_id == sample_receipt.model_id
        assert retrieved.input_hash == sample_receipt.input_hash

    def test_store_duplicate_raises(self, storage, sample_receipt):
        storage.store(sample_receipt)
        with pytest.raises(sqlite3.IntegrityError):
            storage.store(sample_receipt)

    def test_get_nonexistent(self, storage):
        assert storage.get_by_receipt_id("urn:uuid:nonexistent") is None

    def test_list_receipts(self, storage, signer):
        for i in range(3):
            storage.store(
                make(
                    signer,
                    model_id=f"model-{i}@test",
                    input_payload={"i": i},
                    output_payload={"o": i},
                )
            )
        assert len(storage.list_receipts(limit=10)) == 3

    def test_list_by_model(self, storage, signer):
        for i in range(2):
            storage.store(make(signer, input_payload={"i": i}, output_payload={"o": i}))
        storage.store(
            make(
                signer,
                model_id="gpt-4o@openai",
                input_payload={"x": 1},
                output_payload={"y": 1},
            )
        )
        assert len(storage.list_receipts(model_id="claude-sonnet-4@anthropic")) == 2
        assert len(storage.list_receipts(model_id="gpt-4o@openai")) == 1

    def test_list_by_provider(self, storage, signer):
        storage.store(
            make(
                signer,
                model_id="m1@test",
                provider_hub="https://hub1.example.com",
                input_payload={"a": 1},
                output_payload={"a": 1},
            )
        )
        storage.store(
            make(
                signer,
                model_id="m2@test",
                provider_hub="https://hub2.example.com",
                input_payload={"b": 1},
                output_payload={"b": 1},
            )
        )
        assert len(storage.list_receipts(provider_hub="https://hub1.example.com")) == 1
        assert len(storage.list_receipts(provider_hub="https://hub2.example.com")) == 1

    def test_count(self, storage, signer):
        assert storage.count_receipts() == 0
        storage.store(make(signer, model_id="m@test"))
        assert storage.count_receipts() == 1

    def test_stored_receipt_verifies(self, storage, sample_receipt):
        storage.store(sample_receipt)
        retrieved = storage.get_by_receipt_id(sample_receipt.receipt_id)
        assert retrieved is not None
        assert retrieved.verify() is True

    def test_stored_bytes_are_the_document_verbatim(self, storage, sample_receipt):
        """§4.2: verification reads `raw_json`, never a reconstruction from the columns."""
        storage.store(sample_receipt)
        row = storage._conn.execute(
            "SELECT raw_json FROM provenance_receipts WHERE receipt_id = ?",
            (sample_receipt.receipt_id,),
        ).fetchone()
        assert json.loads(row["raw_json"]) == sample_receipt.to_dict()
        retrieved = storage.get_by_receipt_id(sample_receipt.receipt_id)
        assert retrieved.source_bytes is not None


class TestColumnMigration:
    """The schema is unchanged; four columns changed the encoding of new rows."""

    def _row(self, storage, receipt_id):
        return storage._conn.execute(
            "SELECT * FROM provenance_receipts WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()

    def test_schema_is_unchanged(self, storage):
        columns = {
            r[1]
            for r in storage._conn.execute(
                "PRAGMA table_info(provenance_receipts)"
            ).fetchall()
        }
        assert columns == {
            "id",
            "receipt_id",
            "model_id",
            "provider_hub",
            "input_hash",
            "output_hash",
            "parent_receipts",
            "timestamp",
            "issuer_pubkey_b64",
            "proof_value",
            "tee_attestation",
            "latency_ms",
            "price_usd",
            "invocation_nonce",
            "reputation_score",
            "raw_json",
            "created_at",
        }

    def test_awr2_row_encodings(self, storage, signer):
        receipt = make(signer, latency_ms=2340, price_usd=0.15, reputation_score=0.87)
        storage.store(receipt)
        row = self._row(storage, receipt.receipt_id)
        # digests: SRI, not bare hex (§3.2)
        assert row["input_hash"].startswith("sha256-")
        assert row["output_hash"].startswith("sha256-")
        # proofValue: multibase base58btc (§6.1)
        assert row["proof_value"].startswith("z")
        # the key column is derived from the did:key, never an independent assertion (§5.1)
        assert row["issuer_pubkey_b64"] == signer.public_key_b64
        # price_usd stays a float column for querying; the SIGNED value is the string
        assert row["price_usd"] == pytest.approx(0.15)
        assert json.loads(row["raw_json"])["credentialSubject"]["price"]["amount"] == "0.15"
        assert row["latency_ms"] == 2340
        assert row["reputation_score"] == pytest.approx(0.87)

    def test_parent_receipts_column_carries_the_digest(self, storage, signer):
        """An id-only edge is re-pointable (§13.1), so the column keeps the digest too."""
        parent = make(signer)
        storage.store(parent)
        child = make(signer, parent_receipts=[parent])
        storage.store(child)
        row = self._row(storage, child.receipt_id)
        edges = json.loads(row["parent_receipts"])
        assert edges == [{"digestSRI": parent.digest_sri, "id": parent.receipt_id}]

    def test_a_legacy_row_still_reads_and_verifies(self, storage, signer):
        """No row is rewritten: §12.2 forbids re-signing history as AWR/2."""
        document = build_awr1_document(signer)
        legacy = ProvenanceReceipt.from_dict(document)
        storage.store(legacy)

        row = self._row(storage, legacy.receipt_id)
        assert len(row["input_hash"]) == 64  # bare hex, the AWR/1 encoding
        assert not row["proof_value"].startswith("z")  # base64, the AWR/1 encoding
        assert json.loads(row["parent_receipts"]) == []

        retrieved = storage.get_by_receipt_id(legacy.receipt_id)
        assert retrieved is not None
        assert retrieved.is_legacy is True
        assert retrieved.verify() is True

    def test_both_generations_coexist_in_one_table(self, storage, signer):
        storage.store(ProvenanceReceipt.from_dict(build_awr1_document(signer)))
        storage.store(make(signer))
        rows = storage.list_receipts(limit=10)
        assert len(rows) == 2
        assert {r.is_legacy for r in rows} == {True, False}
        assert all(r.verify() for r in rows)
        # Which generation a row is, is readable from the row itself.
        assert {r.proof_type for r in rows} == {
            "Ed25519Signature2018",
            "DataIntegrityProof",
        }
