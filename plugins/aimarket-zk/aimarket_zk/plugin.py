"""aimarket-zk plugin — Zero-knowledge proofs for privacy-preserving AI invocation."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict, deque
from typing import Any, Deque

from aimarket_hub.plugin import HubPlugin
from pydantic import BaseModel, Field


class ProveInputRequest(BaseModel):
    capability_id: str = Field(..., min_length=2)
    input_schema: dict = Field(default_factory=dict)
    input_payload: dict = Field(default_factory=dict)

_PROVE_WINDOW_SEC = 60
_MAX_TRACKED_KEYS = 4096
_prove_attempts: dict[str, Deque[float]] = defaultdict(deque)

_HTTP_DEMO_WARNING = (
    "Plugin demo path only — not a real ZK proof. Configure AIMARKET_ZK_BACKEND with "
    "circom artifacts for production, or AIMARKET_ZK_SIMULATED=1 in non-production dev."
)


def _prove_rate_limit_per_minute() -> int:
    raw = (os.environ.get("AIMARKET_ZK_PROVE_RATE_LIMIT") or "12").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 12


def _prune_expired(now: float) -> None:
    # Bound memory: an attacker rotating through many client keys would otherwise
    # leave one deque per key forever. Only sweep once the table exceeds a cap so
    # the hot path stays O(1).
    if len(_prove_attempts) <= _MAX_TRACKED_KEYS:
        return
    stale = [k for k, w in _prove_attempts.items() if not w or now - w[-1] > _PROVE_WINDOW_SEC]
    for k in stale:
        _prove_attempts.pop(k, None)


def _enforce_prove_rate_limit(client_key: str) -> None:
    from fastapi import HTTPException

    now = time.time()
    window = _prove_attempts[client_key]
    while window and now - window[0] > _PROVE_WINDOW_SEC:
        window.popleft()
    if len(window) >= _prove_rate_limit_per_minute():
        raise HTTPException(status_code=429, detail="ZK prove rate limit exceeded")
    window.append(now)
    _prune_expired(now)


def _trust_forwarded_for() -> bool:
    return (os.environ.get("AIMARKET_ZK_TRUST_FORWARDED_FOR") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _client_key_from_request(request) -> str:
    # X-Forwarded-For is client-controlled and trivially spoofable, so trusting it
    # unconditionally lets an attacker rotate the header to evade the rate limit.
    # Only honour it when explicitly deployed behind a trusted proxy; otherwise use
    # the real socket peer.
    if _trust_forwarded_for():
        forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _make_prover():
    from aimarket_hub.zk_groth16 import make_zk_prover

    return make_zk_prover()


def _configured_zk_backend() -> str:
    return (os.environ.get("AIMARKET_ZK_BACKEND") or "").strip().lower()


def _real_backend_configured() -> bool:
    return _configured_zk_backend() in ("plonk", "groth16")


def _proof_id_from_nullifier(nullifier: str) -> str:
    digest = hashlib.sha256(nullifier.encode()).hexdigest()[:24]
    return f"zk_{digest}"


class ZKPlugin(HubPlugin):
    name = "aimarket-zk"
    version = "2.1.0"
    description = "Zero-knowledge proofs for privacy-preserving AI invocation"
    homepage = "https://github.com/alexar76/aimarket-plugins"
    category = "security"

    def __init__(self):
        super().__init__()
        # Lazily, NOT here. `make_zk_prover()` fails closed unless a real proving backend is
        # configured (AIMARKET_ZK_SIMULATED=1 for the demo path), and building it in __init__
        # meant construction raised — which PluginRegistry.discover() catches and logs, so the
        # plugin was silently ABSENT from the hub rather than present-and-degraded. Verified in
        # a scratch venv and against production: it installs, it never loads, and nothing in the
        # plugin catalogue says why.
        #
        # Same shape as the acex guard in aimarket_hub.capital_pricing: an optional backend that
        # is not configured should cost you that feature's routes at call time, not the whole
        # plugin at import time.
        self._prover_obj: Any = None
        self._prover_failed = False

    def _try_prover(self):
        """Return a configured prover, or None when only the labeled HTTP demo path is available."""
        from fastapi import HTTPException

        if self._prover_failed:
            if _real_backend_configured():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"ZK backend {_configured_zk_backend()} is configured but the prover "
                        "is unavailable — check snarkjs, artifacts, and hub logs."
                    ),
                )
            return None
        if self._prover_obj is not None:
            return self._prover_obj
        try:
            self._prover_obj = _make_prover()
            return self._prover_obj
        except Exception as exc:
            if _real_backend_configured():
                raise HTTPException(
                    status_code=503,
                    detail=f"ZK prover init failed: {exc}",
                ) from exc
            self._prover_failed = True
            return None

    def _http_demo_stats(self) -> dict[str, Any]:
        meta = self.get_manifest_extension().get("zk", {})
        return {
            "simulated": True,
            "warning": _HTTP_DEMO_WARNING,
            "nullifiers_used": 0,
            "mode": "plugin_http_demo",
            **meta,
        }

    def _http_demo_prove(
        self, capability_id: str, input_schema: dict, input_payload: dict
    ) -> dict[str, Any]:
        schema_hash = hashlib.sha256(
            json.dumps(input_schema, sort_keys=True).encode()
        ).hexdigest()
        input_json = json.dumps(input_payload, sort_keys=True)
        ts = time.time()
        input_commitment = hashlib.sha256(
            f"simulated_commitment:{input_json}:{int(ts)}".encode()
        ).hexdigest()
        nullifier = hashlib.sha256(
            f"{capability_id}:{input_commitment}:{ts}".encode()
        ).hexdigest()[:32]
        return {
            "proof_id": f"zk_demo_{int(ts)}",
            "input_commitment": input_commitment,
            "nullifier": nullifier,
            "schema_hash": schema_hash,
            "backend": "simulated",
            "simulated": True,
            "warning": _HTTP_DEMO_WARNING,
            "mode": "plugin_http_demo",
        }

    def register_routes(self, router):
        @router.post("/zk/prove-input")
        async def submit_zk_input(prove_request: ProveInputRequest):
            _enforce_prove_rate_limit(prove_request.capability_id)
            prover = self._try_prover()
            if prover is None:
                return self._http_demo_prove(
                    prove_request.capability_id,
                    prove_request.input_schema,
                    prove_request.input_payload,
                )
            proof = prover.prove_input(
                prove_request.capability_id,
                prove_request.input_schema,
                prove_request.input_payload,
            )
            backend = getattr(proof, "backend", None) or _configured_zk_backend() or "plonk"
            return {
                "proof_id": _proof_id_from_nullifier(proof.nullifier),
                "input_commitment": proof.input_commitment,
                "nullifier": proof.nullifier,
                "schema_hash": proof.schema_hash,
                "backend": backend,
                "simulated": False,
            }

        @router.get("/zk/stats")
        async def zk_stats():
            prover = self._try_prover()
            if prover is None:
                return self._http_demo_stats()
            return prover.stats()

    def get_manifest_extension(self):
        backend = _configured_zk_backend() or "simulated"
        if backend == "plonk":
            scheme = "PLONK"
        elif backend == "groth16":
            scheme = "Groth16"
        else:
            scheme = "simulated"
        production = (
            "circom + snarkjs bn128"
            if backend in ("plonk", "groth16")
            else "labeled HTTP demo (not cryptographic ZK)"
        )
        return {
            "zk": {
                "scheme": scheme,
                "backend": backend,
                "production": production,
            }
        }
