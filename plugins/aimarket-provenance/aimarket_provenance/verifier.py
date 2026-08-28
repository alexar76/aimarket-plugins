"""Verification of provenance receipts (``awr/SPEC.md`` §6.3, §11.1).

No FastAPI and no database dependency, so the API, a CLI and a test can all use it.

This module **does not verify anything itself**.  Signature checking, canonicalization,
``did:key`` derivation, chain resolution and the reason-code registry are all
``awr.verify_document`` (SPEC.md §6.3, §8, §11.2); AWR/1 documents already in storage go
to :func:`aimarket_provenance.legacy.verify_legacy_receipt` (§12).  What is left here is
the hub's own policy layer on top of the format:

* the ``checks``/``errors`` result shape the plugin's API has always returned, so the
  ``/verify/{id}`` response and anything reading it keep working;
* **issuer binding** — the one question AWR deliberately does not answer.  A valid
  document means "this issuer signed these claims" and nothing more (§13.7); whether that
  issuer is the hub the caller thinks it is talking to is a trust-layer decision, and
  AWR/2 has no registry and no revocation by design (§13.5, §13.6).
* an **optional** age policy.  It is off by default, because SPEC.md §11.3 is explicit
  that age is not a validity property and that a two-year-old receipt is exactly as sound
  as today's.  The pre-migration verifier hard-failed anything older than 90 days, which
  Appendix D lists as an AWR/1 defect: an audit is the main reason old receipts are read.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from ._awr import parse_did_key
from .receipt import ProvenanceReceipt

@dataclass
class VerificationResult:
    """Result of a complete provenance receipt verification.

    ``valid``/``checks``/``errors`` are the pre-migration shape and are unchanged.
    ``warnings`` and ``awr`` are additive: ``awr`` is the verbatim SPEC.md §11.1 result,
    which is the only output another AWR implementation can be compared against.
    """

    valid: bool = False
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    awr: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "checks": self.checks,
            "errors": self.errors,
            "warnings": self.warnings,
            "awr": self.awr,
        }


def _describe(entry: dict[str, Any]) -> str:
    return "%s: %s" % (entry.get("code", "?"), entry.get("detail", ""))


def verify_receipt(
    receipt: ProvenanceReceipt,
    signer: Any = None,
    trusted_issuer_keys: dict[str, str] | None = None,
    *,
    parents: list[Any] | None = None,
    profile: str | None = None,
    max_age_seconds: int | None = None,
    now: Any = None,
) -> VerificationResult:
    """Verify a provenance receipt end-to-end.

    Args:
        receipt: the receipt to check.  An AWR/1 document read out of storage is verified
            under the §12 legacy rules and reported as such.
        signer: accepted for source compatibility and **ignored**.  AWR/2 derives the
            public key from ``issuer.id`` (§5.1): a verifier never chooses a key, and a
            key passed in from outside could only be used to check a document against a
            key it does not name.
        trusted_issuer_keys: issuer identifier → pinned base64 Ed25519 public key.  The
            identifier may be the ``did:key`` itself, the issuer/hub name, or the
            ``providerHub`` value.  When an entry matches, the document's key MUST equal
            the pinned one.
        parents: supporting documents for chain resolution (§8.2).  Nothing is ever
            fetched — §13.5 forbids a verifier from dereferencing anything, so an edge
            whose parent was not supplied is reported ``unresolved``, which is not an
            error.
        profile: ``"L0"``/``"L1"``/``"L2"`` to request a profile (§10).  ``AWR-PROFILE-*``
            codes are only reported for a profile the caller asked for (§10.4).
        max_age_seconds: optional caller policy.  ``None`` (default) means age is not
            checked, per §11.3.
        now: fixes "now" so the time warnings are deterministic.
    """
    result = VerificationResult()

    supporting = [
        p.document if isinstance(p, ProvenanceReceipt) else p for p in (parents or [])
    ]
    kwargs: dict[str, Any] = {}
    if now is not None:
        kwargs["now"] = now
    if profile is not None and not receipt.is_legacy:
        kwargs["profile"] = profile

    awr_result = receipt.verify_result(supporting=supporting or None, **kwargs)
    result.awr = awr_result

    errors = list(awr_result.get("reasons") or [])
    warnings = list(awr_result.get("warnings") or [])
    result.errors = [_describe(e) for e in errors]
    result.warnings = [_describe(w) for w in warnings]
    codes = {e.get("code") for e in errors}
    warning_codes = {w.get("code") for w in warnings}

    # ── 1. Structure (SPEC.md §3.1 envelope + §3.3 subject) ──────────────────
    structure_codes = {
        code
        for code in codes
        if isinstance(code, str)
        and (code.startswith("AWR-DOC-") or code.startswith("AWR-RCPT-"))
    }
    result.checks.append(
        {
            "check": "structure",
            "passed": not structure_codes,
            "codes": sorted(structure_codes),
        }
    )

    # ── 2. Signature (§6.3 step 6) ───────────────────────────────────────────
    #
    # `verifiedProof` is a function of the codes reported (§11.1): it is the index of the
    # proof that verified, and `null` whenever any code prevented step 6 from running.
    # That makes it, and not the absence of a specific code, the honest answer to "was the
    # signature checked and did it pass".  An AWR/1 signature is not a §6.1 proof, so a
    # legacy document reports `null` and its outcome is `valid` plus AWR-LEGACY-001.
    if receipt.is_legacy:
        signature_passed = bool(awr_result.get("legacy")) and not codes
        signature_info: dict[str, Any] = {
            "algorithm": "Ed25519",
            "proof_type": receipt.proof_type,
            "legacy": True,
            "legacy_dialect": awr_result.get("legacyDialect"),
            "unsigned_fields": awr_result.get("unsignedFields") or [],
        }
    else:
        signature_passed = awr_result.get("verifiedProof") is not None
        signature_info = {
            "algorithm": "Ed25519",
            "cryptosuite": receipt.cryptosuite,
            "proof_type": receipt.proof_type,
            "verified_proof": awr_result.get("verifiedProof"),
            "covers": "whole document",
        }
    signature_info.update({"check": "signature", "passed": signature_passed})
    signature_info["public_key"] = receipt.issuer_public_key_b64
    result.checks.append(signature_info)

    # ── 3. Issuer binding — the question AWR does not answer (§13.7) ─────────
    #
    # A valid signature proves that *some* keypair signed the document.  Under AWR/2 the
    # key is derived from `issuer.id`, so a forgery cannot present the hub's DID while
    # signing with its own key — it has to state a different issuer, and pinning is what
    # notices.  AWR/1 let the two disagree, which is why this check existed at all.
    bound, binding = _check_issuer_binding(receipt, trusted_issuer_keys)
    result.checks.append(binding)
    if bound is False:
        result.errors.append(
            "Issuer key mismatch: %s is not the pinned key for %s"
            % (receipt.issuer_public_key_b64 or "(no key)", binding.get("issuer"))
        )

    # ── 4. Time (§11.3: age is policy, not validity) ─────────────────────────
    time_codes = sorted(c for c in warning_codes if isinstance(c, str) and c.startswith("AWR-TIME-"))
    time_check: dict[str, Any] = {
        "check": "timestamp",
        "passed": True,
        "value": receipt.timestamp,
        "codes": time_codes,
    }
    if max_age_seconds is not None:
        age = _age_seconds(receipt, now)
        time_check["age_seconds"] = age
        time_check["max_age_seconds"] = max_age_seconds
        if age is not None and age > max_age_seconds:
            time_check["passed"] = False
            result.errors.append(
                "Receipt is older than the caller's %d-second policy (age %d s). "
                "This is policy, not validity: SPEC.md §11.3 makes age a warning."
                % (max_age_seconds, age)
            )
    result.checks.append(time_check)

    # ── 5. Digest format (§3.2 SRI, or AWR/1 hex) ────────────────────────────
    digest_codes = sorted(c for c in codes if c == "AWR-RCPT-001")
    result.checks.append(
        {
            "check": "hash_format",
            "passed": not digest_codes,
            "input": receipt.input_hash,
            "output": receipt.output_hash,
            "payload_serialization": receipt.payload_serialization,
            "codes": digest_codes,
        }
    )

    # ── 6. Chain edges (§8) ──────────────────────────────────────────────────
    chain = awr_result.get("chain") or {}
    if receipt.parents or receipt.parent_receipts:
        chain_codes = sorted(
            c for c in codes if isinstance(c, str) and c.startswith("AWR-CHAIN-")
        )
        result.checks.append(
            {
                "check": "parent_receipts",
                "passed": not chain_codes,
                "count": len(receipt.parents) or len(receipt.parent_receipts),
                "resolved": chain.get("resolved", 0),
                "unresolved": chain.get("unresolved", 0),
                "content_addressed": not receipt.is_legacy,
                "codes": chain_codes,
            }
        )

    # ── 7. Attestations are opaque (§7.3) ────────────────────────────────────
    #
    # The pre-migration verifier checked a TEE attestation's inner signature with the
    # *receipt issuer's* key.  That is worse than no check: it proves only that the party
    # making the claim also wrote it down, while presenting as hardware evidence
    # (§7.3).  Verifying one needs the platform's certificate chain, which is a network
    # operation an offline verifier must not perform, so AWR-ENV-001 is the correct and
    # honest outcome and this check never claims more.
    if receipt.tee_attestation or receipt.zk_input_proof or receipt.zk_output_proof:
        result.checks.append(
            {
                "check": "tee_attestation",
                "passed": True,
                "verified": False,
                "platform": (receipt.tee_attestation or {}).get("platform", ""),
                "note": (
                    "present and inside the signature, but NOT verified: that needs the "
                    "hardware vendor's certificate chain, which an offline verifier must "
                    "not fetch (SPEC.md §7.3, AWR-ENV-001)"
                ),
                "codes": sorted(c for c in warning_codes if c == "AWR-ENV-001"),
            }
        )

    result.valid = bool(awr_result.get("valid")) and not result.errors
    return result


def _check_issuer_binding(
    receipt: ProvenanceReceipt, trusted_issuer_keys: dict[str, str] | None
) -> tuple[bool | None, dict[str, Any]]:
    """``(bound, check)``. ``bound`` is ``None`` when no pin applies to this issuer."""
    identifiers = [
        value
        for value in (receipt.issuer_id, receipt.issuer_name, receipt.provider_hub)
        if value
    ]
    pins = trusted_issuer_keys or {}
    matched = next((name for name in identifiers if name in pins), None)
    embedded = receipt.issuer_public_key_b64

    check: dict[str, Any] = {
        "check": "issuer_binding",
        "issuer": receipt.issuer_id or receipt.provider_hub,
        "did_key": receipt.issuer_did,
    }
    if matched is None:
        check.update(
            {
                "passed": True,
                "bound": False,
                "note": (
                    "no pinned key for this issuer. AWR/2 derives the key from issuer.id "
                    "(§5.1) so the document cannot contradict its own DID, but whether "
                    "that DID is the party you meant is a trust-layer question AWR does "
                    "not answer (§13.7)"
                ),
            }
        )
        return None, check

    pinned = pins[matched]
    bound = bool(embedded) and _same_key(pinned, embedded)
    check.update({"passed": bound, "bound": bound, "pinned_as": matched})
    return bound, check


def _same_key(pinned: str, embedded: str) -> bool:
    """Compare two key encodings by their raw bytes.

    A pin may be given as base64 raw key or as the ``did:key`` itself; comparing the
    strings would make ``did:key:z6Mk…`` and its own base64 key look like different keys.
    """
    return _raw_key(pinned) == _raw_key(embedded) != b""


def _raw_key(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        return b""
    if value.startswith("did:key:"):
        try:
            return parse_did_key(value)
        except Exception:
            return b""
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        return b""
    return raw if len(raw) == 32 else b""


def _age_seconds(receipt: ProvenanceReceipt, now: Any) -> int | None:
    from ._awr import awr as _awr_module

    stamp = _awr_module.documents.parse_rfc3339_utc(receipt.timestamp)
    if stamp is None:
        return None
    moment = _awr_module.documents.coerce_now(now)
    return int((moment - stamp).total_seconds())
