"""Which address the ZK prove rate limit buckets on (MOMUS code finding mom-f83d29f3cf254806, 2026-11-16).

X-Forwarded-For counts only when the socket peer is one of AIMARKET_ZK_TRUSTED_PROXIES; the old
yes/no AIMARKET_ZK_TRUST_FORWARDED_FOR trusted it from any caller."""

from __future__ import annotations

import pytest

from aimarket_zk import plugin


class _Req:
    def __init__(self, xff, peer):
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = type("C", (), {"host": peer})() if peer is not None else None


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("AIMARKET_ZK_TRUSTED_PROXIES", raising=False)
    monkeypatch.delenv("AIMARKET_ZK_TRUST_FORWARDED_FOR", raising=False)
    monkeypatch.setattr(plugin, "_warned_old_flag", False)


def test_a_direct_caller_cannot_choose_its_bucket(monkeypatch):
    monkeypatch.setenv("AIMARKET_ZK_TRUSTED_PROXIES", "127.0.0.1/32")
    for forged in ("1.1.1.1", "2.2.2.2, 3.3.3.3"):
        assert plugin._client_key_from_request(_Req(forged, "198.51.100.7")) == "198.51.100.7"


def test_the_configured_proxy_s_hop_is_used_right_most(monkeypatch):
    monkeypatch.setenv("AIMARKET_ZK_TRUSTED_PROXIES", "10.0.0.0/8, 127.0.0.1/32")
    assert plugin._client_key_from_request(_Req("1.1.1.1, 203.0.113.9", "10.1.2.3")) == "203.0.113.9"
    assert plugin._client_key_from_request(_Req("", "10.1.2.3")) == "10.1.2.3"


def test_nothing_configured_means_the_peer(monkeypatch):
    assert plugin._client_key_from_request(_Req("1.1.1.1", "127.0.0.1")) == "127.0.0.1"
    assert plugin._client_key_from_request(_Req("1.1.1.1", None)) == "unknown"


def test_a_bad_cidr_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setenv("AIMARKET_ZK_TRUSTED_PROXIES", "not-a-net,127.0.0.1/32")
    assert plugin._client_key_from_request(_Req("203.0.113.9", "127.0.0.1")) == "203.0.113.9"


def test_the_old_flag_alone_no_longer_trusts_the_header_and_says_so(monkeypatch, caplog):
    monkeypatch.setenv("AIMARKET_ZK_TRUST_FORWARDED_FOR", "1")
    with caplog.at_level("WARNING"):
        assert plugin._client_key_from_request(_Req("1.1.1.1", "198.51.100.7")) == "198.51.100.7"
        plugin._client_key_from_request(_Req("1.1.1.1", "198.51.100.7"))
    assert sum("AIMARKET_ZK_TRUSTED_PROXIES" in r.message for r in caplog.records) == 1
