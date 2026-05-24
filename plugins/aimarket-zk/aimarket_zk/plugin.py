"""aimarket-zk plugin — Zero-knowledge proofs for privacy-preserving AI invocation."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Deque

from aimarket_hub.plugin import HubPlugin

_PROVE_WINDOW_SEC = 60
_prove_attempts: dict[str, Deque[float]] = defaultdict(deque)


def _prove_rate_limit_per_minute() -> int:
    raw = (os.environ.get("AIMARKET_ZK_PROVE_RATE_LIMIT") or "12").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 12


def _enforce_prove_rate_limit(client_key: str) -> None:
    from fastapi import HTTPException

    now = time.time()
    window = _prove_attempts[client_key]
    while window and now - window[0] > _PROVE_WINDOW_SEC:
        window.popleft()
    if len(window) >= _prove_rate_limit_per_minute():
        raise HTTPException(status_code=429, detail="ZK prove rate limit exceeded")
    window.append(now)


def _client_key_from_request(request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _make_prover():
    from aimarket_hub.zk_groth16 import make_zk_prover

    return make_zk_prover()


class ZKPlugin(HubPlugin):
    name = "aimarket-zk"
    version = "2.1.0"
    description = "Zero-knowledge proofs for privacy-preserving AI invocation"
    homepage = "https://github.com/ai-factory/aimarket-zk"
    category = "security"

    def __init__(self):
        super().__init__()
        self._prover = _make_prover()

    def register_routes(self, router):
        from fastapi import Request
        from pydantic import BaseModel, Field

        class ProveInputRequest(BaseModel):
            capability_id: str = Field(..., min_length=2)
            input_schema: dict = Field(default_factory=dict)
            input_payload: dict = Field(default_factory=dict)

        @router.post("/zk/prove-input")
        async def prove_input(body: ProveInputRequest, request: Request):
            _enforce_prove_rate_limit(_client_key_from_request(request))
            proof = self._prover.prove_input(
                body.capability_id, body.input_schema, body.input_payload
            )
            return {
                "proof_id": proof.proof_id,
                "input_commitment": proof.input_commitment,
                "nullifier": proof.nullifier,
                "schema_hash": proof.schema_hash,
                "backend": getattr(proof, "backend", os.environ.get("AIMARKET_ZK_BACKEND", "simulated")),
            }

        @router.get("/zk/stats")
        async def zk_stats():
            return self._prover.stats()

    def get_manifest_extension(self):
        backend = os.environ.get("AIMARKET_ZK_BACKEND", "simulated")
        scheme = "Groth16" if backend == "groth16" else "Groth16 (simulated)"
        return {
            "zk": {
                "scheme": scheme,
                "backend": backend,
                "production": "circom + snarkjs bn128",
            }
        }
