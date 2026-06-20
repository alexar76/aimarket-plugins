"""Tests for the Oracle Gateway routing/parse logic (no MCP runtime, no network).

Run: pip install -e plugins/aimarket-oracle-gateway && pytest plugins/aimarket-oracle-gateway/tests
"""
from __future__ import annotations

import pytest

from aimarket_oracle_gateway.gateway_core import CAPABILITIES, GatewayError, OracleGateway, SpendError


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeClient:
    """Records the last POST and returns a canned response."""

    def __init__(self, data):
        self._data = data
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        return _Resp(self._data)


def test_hub_invoke_builds_body_headers_and_fee():
    fake = _FakeClient({"price_usd": 0.004, "result": {"random_hex": "0xab", "signature": "ed:..", "proof": {"state_hash": "0x1"}}})
    gw = OracleGateway(hub_url="https://hub.example", payment_channel="chan-1", api_token="tok", http=fake)
    out = gw.call_tool("get_random", {"num_bytes": 32, "client_seed": "0xdead"})

    call = fake.calls[-1]
    assert call["url"] == "https://hub.example/ai-market/v2/invoke"
    assert call["json"]["capability_id"] == "platon.random@v1"
    assert call["json"]["product_id"] == "prod-platon"
    assert call["json"]["input"] == {"num_bytes": 32, "client_seed": "0xdead"}
    assert call["headers"]["X-Payment-Channel"] == "chan-1"
    assert call["headers"]["Authorization"] == "Bearer tok"
    assert out["price_usd"] == 0.004
    assert out["routing_fee_usd"] == round(0.004 * 100 / 10_000, 6)
    assert out["source"] == "hub"
    assert out["verifiable"] == {"signed": True, "has_proof": True}
    assert out["result"]["random_hex"] == "0xab"


def test_oracle_direct_invoke_no_fee_no_auth_header():
    fake = _FakeClient({"price_usd": 0.01, "output": {"y": "0x1", "proof": {"pi": "0x2"}}})
    gw = OracleGateway(oracle_url="https://oracles.example", http=fake)
    out = gw.call_tool("compute_vdf", {"seed": "0x1", "difficulty": 1000})

    call = fake.calls[-1]
    assert call["url"] == "https://oracles.example/ai-market/v2/invoke"
    assert call["json"] == {"capability_id": "chronos.eval@v1", "input": {"seed": "0x1", "difficulty": 1000}}
    assert "X-Payment-Channel" not in call["headers"]
    assert out["source"] == "oracle-direct"
    assert out["routing_fee_usd"] == 0.0
    assert out["verifiable"]["has_proof"] is True


def test_price_falls_back_to_hint_when_absent():
    fake = _FakeClient({"result": {"scores": [0.5, 0.5]}})  # no price_usd in response
    gw = OracleGateway(hub_url="https://hub.example", http=fake)
    out = gw.call_tool("get_reputation_scores", {"nodes": 2, "edges": [[0, 1, 1.0]], "damping": 0.85})
    assert out["price_usd"] == CAPABILITIES["get_reputation_scores"].price_usd  # 0.005


def test_no_endpoint_fails_closed():
    gw = OracleGateway(http=_FakeClient({}))
    with pytest.raises(GatewayError, match="No endpoint configured"):
        gw.call_tool("get_random", {})


def test_unknown_tool_rejected():
    gw = OracleGateway(hub_url="https://hub.example", http=_FakeClient({}))
    with pytest.raises(GatewayError, match="unknown tool"):
        gw.call_tool("definitely_not_a_tool", {})


def test_per_call_cap_fails_closed_before_any_call():
    # advertised compute_vdf is $0.01; cap is $0.005 → refuse, and make NO HTTP call
    gw = OracleGateway(hub_url="https://hub.example", http=_FakeClient({}), max_per_call_usd=0.005)
    with pytest.raises(SpendError, match="per-call price"):
        gw.call_tool("compute_vdf", {"seed": "0x1"})
    assert gw.http.calls == []  # fail-closed: nothing was invoked


def test_total_budget_enforced():
    fake = _FakeClient({"price_usd": 0.004, "result": {"random_hex": "0x1"}})
    gw = OracleGateway(hub_url="https://hub.example", http=fake, max_per_call_usd=1.0, max_total_usd=0.01)
    gw.call_tool("get_random", {})  # 0.004 → spent 0.004
    gw.call_tool("get_random", {})  # 0.008
    with pytest.raises(SpendError, match="total budget"):
        gw.call_tool("get_random", {})  # 0.012 > 0.01 cap
    assert gw.spent_usd == pytest.approx(0.008)


def test_hub_overcharge_rejected():
    # advertised get_random $0.004; a malicious hub quotes $0.05 → reject (overcharge guard)
    fake = _FakeClient({"price_usd": 0.05, "result": {}})
    gw = OracleGateway(hub_url="https://hub.example", http=fake, max_per_call_usd=1.0)
    with pytest.raises(SpendError, match="overcharged"):
        gw.call_tool("get_random", {})


def test_within_budget_accrues_spent():
    fake = _FakeClient({"price_usd": 0.004, "result": {"random_hex": "0x1"}})
    gw = OracleGateway(hub_url="https://hub.example", http=fake)
    out = gw.call_tool("get_random", {})
    assert out["price_usd"] == pytest.approx(0.004)
    assert gw.spent_usd == pytest.approx(0.004)


def test_actual_price_budget_check_closes_tolerance_gap():
    # advertised get_random $0.004 passes the precheck (<= $0.0042), but the actual $0.0044 (within
    # the +10% tolerance, so not "overcharge") would breach the cap → refuse on accrual.
    fake = _FakeClient({"price_usd": 0.0044, "result": {}})
    gw = OracleGateway(hub_url="https://hub.example", http=fake, max_per_call_usd=1.0, max_total_usd=0.0042)
    with pytest.raises(SpendError, match="total budget"):
        gw.call_tool("get_random", {})


def test_every_tool_maps_to_a_live_capability():
    # guards the storefront: each MCP tool resolves to a capabilityId + product
    for name, spec in CAPABILITIES.items():
        assert spec.capability_id.endswith("@v1")
        assert spec.product_id.startswith("prod-")
        assert spec.price_usd > 0
