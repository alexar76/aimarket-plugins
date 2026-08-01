"""Tests for ProvenancePlugin — lifecycle, hooks, manifest — and the HTTP surface."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from aimarket_hub.database import HubDatabase
from aimarket_provenance.api import create_provenance_router
from aimarket_provenance.plugin import ProvenancePlugin
from aimarket_provenance.receipt import ProvenanceReceipt
from aimarket_provenance.storage import ProvenanceStorage


@pytest.fixture
def plugin() -> ProvenancePlugin:
    return ProvenancePlugin()


@pytest.fixture
def db() -> HubDatabase:
    with tempfile.TemporaryDirectory() as tmp:
        yield HubDatabase(db_path=str(Path(tmp) / "test_hub.db"))


@pytest.fixture
def storage() -> ProvenanceStorage:
    with tempfile.TemporaryDirectory() as tmp:
        s = ProvenanceStorage(db_path=str(Path(tmp) / "api_provenance.db"))
        yield s
        s.close()


@pytest.fixture
def client(storage, signer) -> TestClient:
    app = FastAPI()
    router = APIRouter(prefix="/ai-market/v2/p/provenance")
    router.include_router(
        create_provenance_router(
            storage=storage,
            signer=signer,
            hub_name="Test Hub",
            hub_version="3.0.0",
            api_token="secret-token",
        )
    )
    app.include_router(router)
    return TestClient(app)


ATTEST = "/ai-market/v2/p/provenance/attest"
AUTH = {"Authorization": "Bearer secret-token"}


class TestPluginMetadata:
    def test_name(self, plugin):
        assert plugin.name == "provenance"

    def test_version(self, plugin):
        assert plugin.version == "2.0.0"

    def test_version_matches_the_distribution(self, plugin):
        """The class and the dist must agree, or the hub advertises a version PyPI lacks."""
        text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        assert 'version = "%s"' % (plugin.version,) in text

    def test_category(self, plugin):
        assert plugin.category == "compliance"


class TestPluginLifecycle:
    def test_on_startup(self, plugin, db):
        plugin.on_startup(db)
        assert plugin._storage is not None

    def test_register_routes(self, plugin, db, monkeypatch, tmp_path):
        monkeypatch.setenv("AIMARKET_PROVENANCE_KEY_PATH", str(tmp_path / "key"))
        plugin.on_startup(db)
        router = APIRouter()
        plugin.register_routes(router)
        paths = {route.path for route in router.routes}
        assert paths == {
            "/attest",
            "/receipt/{receipt_id:path}",
            "/verify/{receipt_id:path}",
        }


class TestPluginHooks:
    def test_get_manifest_extension(self, plugin):
        ext = plugin.get_manifest_extension()["provenance"]
        assert ext["version"] == "2.0.0"
        assert ext["receipt_format"] == "AWR/2 (W3C Verifiable Credential)"
        assert ext["awr_version"] == "2.0.0"
        assert ext["proof_type"] == "DataIntegrityProof"
        assert ext["cryptosuite"] == "eddsa-jcs-2022"
        assert ext["canonicalization"] == "RFC 8785 (JCS)"
        assert ext["issuer_identity"] == "did:key"
        assert ext["signing_algorithm"] == "Ed25519"
        assert "endpoints" in ext
        assert ext["features"]["auto_receipt"] is True
        assert ext["features"]["tee_attestation"] is True
        # §7.3: an attestation is carried, never verified. Advertising otherwise is the
        # misrepresentation that section was written about.
        assert ext["features"]["attestations_verified"] is False
        # §12: verification of stored AWR/1 documents stays; issuance is gone.
        assert ext["features"]["legacy_awr1_verification"] is True
        assert ext["features"]["legacy_awr1_issuance"] is False

    def test_auto_receipt_generation(self, plugin, db, monkeypatch, tmp_path):
        """The invoke post-check generates, stores and reports an AWR/2 receipt."""
        monkeypatch.setenv("AIMARKET_PROVENANCE_KEY_PATH", str(tmp_path / "key"))
        monkeypatch.setenv("AIMARKET_HUB_URL", "https://hub.example.com")
        plugin = ProvenancePlugin()  # re-read the env
        plugin.on_startup(db)
        plugin.register_routes(APIRouter())

        output = {"text": "Generated AI response"}
        context = {
            "product_id": "prod-test",
            "capability_id": "text-gen",
            "input": {"prompt": "Hello"},
            "provider_hub": "local",
            "latency_ms": 1500,
            "price_usd": 0.05,
        }
        assert plugin.on_invoke_post_check(output, context) is None

        pr = output.get("_provenance_receipt")
        assert pr is not None
        # The two keys the hub's `provenance_receipt` contract has always carried.
        assert pr["receipt_id"].startswith("urn:uuid:")
        assert "verify_url" in pr
        # Both hub URLs are routes this plugin actually serves. The previous
        # `{verify_domain}/r/{short_id}` was a dead deep link: the static verifier reads
        # no path, so it opened an empty form (see README).
        assert pr["verify_url"] == (
            "https://hub.example.com/ai-market/v2/p/provenance/verify/%s"
            % (pr["receipt_id"],)
        )
        assert pr["receipt_url"] == (
            "https://hub.example.com/ai-market/v2/p/provenance/receipt/%s"
            % (pr["receipt_id"],)
        )
        assert pr["verifier_url"] == "https://verify.modelmarket.dev"
        assert pr["awr_version"] == "2.0.0"
        assert pr["issuer"].startswith("did:key:z6Mk")

        stored = plugin._storage.get_by_receipt_id(pr["receipt_id"])
        assert stored is not None and stored.verify() is True
        assert stored.model_id == "text-gen@prod-test"

    def test_verify_url_is_relative_without_a_hub_url(self, plugin, db, monkeypatch, tmp_path):
        monkeypatch.setenv("AIMARKET_PROVENANCE_KEY_PATH", str(tmp_path / "key2"))
        monkeypatch.delenv("AIMARKET_HUB_URL", raising=False)
        plugin = ProvenancePlugin()
        plugin.on_startup(db)
        plugin.register_routes(APIRouter())
        output: dict = {}
        plugin.on_invoke_post_check(output, {"capability_id": "c", "input": {}})
        assert output["_provenance_receipt"]["verify_url"].startswith(
            "/ai-market/v2/p/provenance/verify/"
        )


class TestApi:
    def test_attest_requires_auth(self, client):
        assert client.post(ATTEST, json={}).status_code == 401
        response = client.post(
            ATTEST,
            json={"model_id": "m", "input": {}, "output": {}},
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 403

    def test_attest_returns_an_awr2_document(self, client):
        response = client.post(
            ATTEST,
            json={
                "model_id": "legal.review@v1@prod-legal",
                "provider_hub": "https://provider.example.com",
                "input": {"documents": {"hash": "sha256:..."}},
                "output": {"risk": "low", "issues": 0},
                "latency_ms": 4200,
                "price_usd": 0.15,
            },
            headers=AUTH,
        )
        assert response.status_code == 200, response.text
        document = response.json()
        assert document["awrVersion"] == "2.0.0"
        assert document["proof"]["cryptosuite"] == "eddsa-jcs-2022"
        assert document["issuer"]["id"].startswith("did:key:z6Mk")
        assert document["credentialSubject"]["price"] == {
            "currency": "USD",
            "amount": "0.15",
        }

    def test_attest_missing_fields(self, client):
        response = client.post(ATTEST, json={"model_id": "m"}, headers=AUTH)
        assert response.status_code == 400
        assert "input" in response.json()["detail"]

    def test_attest_rejects_an_unresolvable_parent(self, client):
        """§3.2/§13.1: the hub cannot sign a commitment to bytes it has never seen."""
        response = client.post(
            ATTEST,
            json={
                "model_id": "m",
                "input": {},
                "output": {},
                "parent_receipts": ["urn:uuid:not-here"],
            },
            headers=AUTH,
        )
        assert response.status_code == 400
        assert "content-addressed" in response.json()["detail"]

    def test_attest_resolves_a_stored_parent_into_a_digest_edge(self, client):
        first = client.post(
            ATTEST, json={"model_id": "m1", "input": {"a": 1}, "output": {"b": 2}}, headers=AUTH
        ).json()
        second = client.post(
            ATTEST,
            json={
                "model_id": "m2",
                "input": {"b": 2},
                "output": {"c": 3},
                "parent_receipts": [first["id"]],
            },
            headers=AUTH,
        )
        assert second.status_code == 200, second.text
        parents = second.json()["credentialSubject"]["parents"]
        assert parents[0]["id"] == first["id"]
        assert parents[0]["digestSRI"].startswith("sha256-")

    def test_receipt_and_verify_round_trip(self, client):
        created = client.post(
            ATTEST,
            json={"model_id": "m@p", "input": {"a": 1}, "output": {"b": 2}},
            headers=AUTH,
        ).json()
        receipt_id = created["id"]

        fetched = client.get("/ai-market/v2/p/provenance/receipt/%s" % (receipt_id,))
        assert fetched.status_code == 200
        assert fetched.json() == created

        verified = client.get("/ai-market/v2/p/provenance/verify/%s" % (receipt_id,))
        assert verified.status_code == 200
        body = verified.json()
        assert body["valid"] is True
        assert body["awr_version"] == "2.0.0"
        assert body["format"] == "AWR/2"
        assert body["cryptosuite"] == "eddsa-jcs-2022"
        assert body["awr"]["profile"] == "L0"
        assert body["awr"]["verifiedProof"] == 0
        binding = next(c for c in body["checks"] if c["check"] == "issuer_binding")
        assert binding["bound"] is True  # pinned against this hub's own did:key

    def test_verify_reports_a_chain(self, client):
        first = client.post(
            ATTEST, json={"model_id": "m1", "input": {"a": 1}, "output": {"b": 2}}, headers=AUTH
        ).json()
        second = client.post(
            ATTEST,
            json={
                "model_id": "m2",
                "input": {"b": 2},
                "output": {"c": 3},
                "parent_receipts": [first["id"]],
            },
            headers=AUTH,
        ).json()
        body = client.get(
            "/ai-market/v2/p/provenance/verify/%s" % (second["id"],)
        ).json()
        assert body["valid"] is True
        # §8.2: resolved against documents this hub already holds — nothing is fetched.
        assert body["awr"]["chain"] == {"resolved": 1, "unresolved": 0}

    def test_verify_a_stored_legacy_receipt(self, client, storage, signer):
        from .conftest import build_awr1_document

        legacy = ProvenanceReceipt.from_dict(build_awr1_document(signer))
        storage.store(legacy)
        body = client.get(
            "/ai-market/v2/p/provenance/verify/%s" % (legacy.receipt_id,)
        ).json()
        assert body["valid"] is True
        assert body["format"] == "AWR/1 (legacy, verify-only)"
        assert body["awr_version"] is None
        assert body["awr"]["profile"] is None
        assert any("AWR-LEGACY-001" in w for w in body["warnings"])

    def test_verify_missing_receipt(self, client):
        assert client.get("/ai-market/v2/p/provenance/verify/urn:uuid:nope").status_code == 404

    def test_attest_is_disabled_without_a_token(self, storage, signer):
        app = FastAPI()
        app.include_router(
            create_provenance_router(storage=storage, signer=signer, api_token="")
        )
        response = TestClient(app).post("/attest", json={})
        assert response.status_code == 503
