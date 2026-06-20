"""Pure, testable core for the AIMarket Oracle Gateway MCP server.

It maps agent-legible MCP tool names to real oracle capabilities and invokes them over
the AIMarket v2 protocol — through the Hub (`POST {hub}/ai-market/v2/invoke`, which meters
the call + takes the routing fee that fuels the ecosystem) or directly against the
oracle-family (`POST {oracle}/ai-market/v2/invoke`). No MCP runtime is imported here, so
the routing/parse logic is unit-testable with an injected HTTP client.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


class GatewayError(RuntimeError):
    """Raised on misconfiguration or an unknown tool (never swallows transport errors)."""


class SpendError(GatewayError):
    """Raised when a call would breach a spending cap, or the hub overcharges vs the advertised
    price. This is the client-side guard that a prompt-injected / runaway agent cannot override —
    the gateway simply refuses to spend past the configured limits (fail-closed)."""


@dataclass(frozen=True)
class CapabilitySpec:
    capability_id: str   # AIMarket capabilityId, e.g. "platon.random@v1"
    product_id: str      # owning product on the Hub, e.g. "prod-platon"
    price_usd: float     # advertised price per call (the Hub returns the authoritative price)
    summary: str         # one-liner surfaced as the MCP tool description


# tool name → capability. Tool names are chosen to be legible to an autonomous agent
# browsing an MCP client; each maps 1:1 to a live, signed, priced oracle capability.
CAPABILITIES: dict[str, CapabilitySpec] = {
    "get_random": CapabilitySpec(
        "platon.random@v1", "prod-platon", 0.004,
        "Unbiasable, Ed25519-signed verifiable randomness with a proof you can verify.",
    ),
    "get_randomness_beacon": CapabilitySpec(
        "platon.beacon@v1", "prod-platon", 0.004,
        "Latest public randomness beacon (shared across callers in a round).",
    ),
    "ask_oracle": CapabilitySpec(
        "platon.ask@v1", "prod-platon", 0.003,
        "Grounded, entropy-derived read-only answer to a question.",
    ),
    "verify_random": CapabilitySpec(
        "platon.verify@v1", "prod-platon", 0.001,
        "Verify a Platon randomness draw (random_hex + proof + signature) against the signer key.",
    ),
    "compute_vdf": CapabilitySpec(
        "chronos.eval@v1", "prod-chronos", 0.01,
        "Verifiable Delay Function: proves T sequential squarings (~elapsed time) over RSA-2048.",
    ),
    "verify_vdf": CapabilitySpec(
        "chronos.verify@v1", "prod-chronos", 0.001,
        "Verify a VDF proof in one exponentiation.",
    ),
    "get_reputation_scores": CapabilitySpec(
        "lumen.reputation@v1", "prod-lumen", 0.005,
        "PageRank/EigenTrust trust scores over a directed trust graph you supply.",
    ),
    "get_agent_trust": CapabilitySpec(
        "lumen.score@v1", "prod-lumen", 0.003,
        "Trust score, rank, and percentile of one target node in a trust graph you supply.",
    ),
    "verify_reputation": CapabilitySpec(
        "lumen.verify@v1", "prod-lumen", 0.002,
        "Re-derive PageRank over a supplied graph and confirm claimed scores / graph_commitment.",
    ),
}


def _unwrap(data: Any) -> Any:
    """Unwrap the invoke response. Two envelopes can nest: the Hub wraps in `result`, the oracle
    in `output` (the relayer's hub path reads `result` first, then unwraps `output`). Check both so
    the gateway works against the Hub and a direct oracle; fall back to the body itself."""
    if isinstance(data, dict):
        for key in ("result", "output"):
            if isinstance(data.get(key), dict):
                return data[key]
        return data
    return {"value": data}


def _verifiable(result: Any) -> dict:
    """Surface the verifiability handles an agent can check (signature / VDF proof)."""
    if not isinstance(result, dict):
        return {"signed": False, "has_proof": False}
    return {
        "signed": bool(result.get("signature")),
        "has_proof": bool(result.get("proof") or result.get("pi")),
    }


@dataclass
class OracleGateway:
    """Routes MCP tool calls to AIMarket oracle capabilities."""

    hub_url: str = ""
    oracle_url: str = ""
    payment_channel: str = ""
    payment_channel_secret: str = ""  # per-channel debit secret (X-Payment-Channel-Secret)
    api_token: str = ""
    source_hub: str = "mcp-oracle-gateway"
    routing_fee_bps: int = 100  # the Hub's default 1%
    # Spending caps — the prompt-injection / runaway-agent drain defense (spec 03 §3a). The model
    # cannot override these; the gateway refuses to spend past them (fail-closed).
    max_per_call_usd: float = 0.10   # reject any single call advertised above this
    max_total_usd: float = 5.0       # reject once cumulative spend would exceed this
    price_tolerance: float = 0.10    # reject if the hub charges > advertised * (1 + this)
    spent_usd: float = 0.0           # running total this process
    http: Any = None  # injectable httpx.Client-like (lazy real client if None)

    @classmethod
    def from_env(cls) -> "OracleGateway":
        def _f(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, str(default)))
            except ValueError:
                return default

        return cls(
            hub_url=os.getenv("AIMARKET_HUB_URL", "").rstrip("/"),
            oracle_url=os.getenv("AIMARKET_ORACLE_URL", "").rstrip("/"),
            payment_channel=os.getenv("AIMARKET_PAYMENT_CHANNEL", ""),
            payment_channel_secret=os.getenv("AIMARKET_PAYMENT_CHANNEL_SECRET", ""),
            api_token=os.getenv("AIMARKET_API_TOKEN", ""),
            max_per_call_usd=_f("AIMARKET_MAX_PER_CALL_USD", 0.10),
            max_total_usd=_f("AIMARKET_MAX_SPEND_USD", 5.0),
            price_tolerance=_f("AIMARKET_PRICE_TOLERANCE", 0.10),
        )

    def _precheck_budget(self, price_hint: float) -> None:
        """Fail-closed BEFORE the paid call: per-call cap + cumulative budget on the advertised price."""
        if price_hint > self.max_per_call_usd:
            raise SpendError(
                f"per-call price ${price_hint} exceeds cap ${self.max_per_call_usd} "
                f"(raise AIMARKET_MAX_PER_CALL_USD to allow)"
            )
        if self.spent_usd + price_hint > self.max_total_usd:
            raise SpendError(
                f"call would exceed total budget ${self.max_total_usd} (spent ${self.spent_usd:.4f}; "
                f"raise AIMARKET_MAX_SPEND_USD to allow)"
            )

    def _account(self, price_usd: float, price_hint: float) -> None:
        """AFTER the call: reject overcharge vs advertised, else accrue to the running total."""
        if price_hint > 0 and price_usd > price_hint * (1.0 + self.price_tolerance):
            raise SpendError(
                f"hub overcharged: charged ${price_usd} > advertised ${price_hint} "
                f"+{int(self.price_tolerance * 100)}% tolerance"
            )
        # Enforce the budget against the ACTUAL charged price too — the per-call tolerance must not
        # let cumulative spend slip past the cap.
        if self.spent_usd + price_usd > self.max_total_usd:
            raise SpendError(
                f"call would exceed total budget ${self.max_total_usd} (actual ${price_usd}; "
                f"spent ${self.spent_usd:.4f})"
            )
        self.spent_usd = round(self.spent_usd + price_usd, 6)

    def _client(self):
        if self.http is None:
            import httpx  # imported lazily so tests can inject a fake client

            self.http = httpx.Client(timeout=30.0)
        return self.http

    def _post(self, url: str, body: dict, headers: dict) -> dict:
        # Redact the endpoint to host-only in any raised error: a hub/oracle URL configured with
        # embedded credentials (https://key@host) must never leak via an exception to the MCP
        # client or logs. `from None` drops the original (URL-bearing) exception chain.
        try:
            resp = self._client().post(url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            import urllib.parse
            try:
                p = urllib.parse.urlsplit(url)
                host = f"{p.scheme}://{p.hostname or '?'}" if p.scheme else "<endpoint>"
            except Exception:  # noqa: BLE001
                host = "<endpoint>"
            status = getattr(getattr(exc, "response", None), "status_code", None)
            detail = f"HTTP {status}" if status else type(exc).__name__
            raise GatewayError(f"request to {host} failed: {detail}") from None

    def invoke(self, capability_id: str, payload: dict, *, product_id: str = "", price_hint: float = 0.0) -> dict:
        """Invoke one capability; returns {capability_id, result, price_usd, routing_fee_usd, source, verifiable}."""
        self._precheck_budget(price_hint)  # fail-closed BEFORE any paid call (drain/cap defense)
        if self.hub_url:
            body = {
                "product_id": product_id,
                "capability_id": capability_id,
                "source_hub": self.source_hub,
                "input": payload,
            }
            headers: dict[str, str] = {}
            if self.payment_channel:
                headers["X-Payment-Channel"] = self.payment_channel
                if self.payment_channel_secret:
                    headers["X-Payment-Channel-Secret"] = self.payment_channel_secret
            if self.api_token:
                headers["Authorization"] = f"Bearer {self.api_token}"
            data = self._post(f"{self.hub_url}/ai-market/v2/invoke", body, headers)
            price = float(data.get("price_usd", price_hint) or price_hint)
            result = _unwrap(data)
            self._account(price, price_hint)  # reject overcharge vs advertised, else accrue
            return {
                "capability_id": capability_id,
                "result": result,
                "price_usd": price,
                "routing_fee_usd": round(price * self.routing_fee_bps / 10_000, 6),
                "source": "hub",
                "verifiable": _verifiable(result),
            }
        if self.oracle_url:
            data = self._post(f"{self.oracle_url}/ai-market/v2/invoke", {"capability_id": capability_id, "input": payload}, {})
            price = float(data.get("price_usd", price_hint) or price_hint)
            result = _unwrap(data)
            self._account(price, price_hint)  # reject overcharge vs advertised, else accrue
            return {
                "capability_id": capability_id,
                "result": result,
                "price_usd": price,
                "routing_fee_usd": 0.0,
                "source": "oracle-direct",
                "verifiable": _verifiable(result),
            }
        raise GatewayError(
            "No endpoint configured. Set AIMARKET_HUB_URL (paid via the protocol, recommended — "
            "the routing fee funds the ecosystem) or AIMARKET_ORACLE_URL (direct, demo/free)."
        )

    def call_tool(self, tool: str, payload: dict) -> dict:
        spec = CAPABILITIES.get(tool)
        if spec is None:
            raise GatewayError(f"unknown tool {tool!r}; known tools: {sorted(CAPABILITIES)}")
        return self.invoke(spec.capability_id, payload, product_id=spec.product_id, price_hint=spec.price_usd)
