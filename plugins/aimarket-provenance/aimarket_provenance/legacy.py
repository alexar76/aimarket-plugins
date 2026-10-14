"""AWR/1 legacy verification for receipts already in the database (SPEC.md §12).

Every function here is **verification only**.  SPEC.md §12 states that an implementation
supporting AWR/1 "MUST NOT issue AWR/1 documents", so this module contains no signing
path at all: `sign` is absent rather than guarded, exactly as in ``awr.legacy``.

Why the canonical rendering still lives in this plugin
-----------------------------------------------------

SPEC.md §12.1 writes out an AWR/1 pipe-delimited rendering as ``path=leaf`` entries with
dotted leaf paths.  The receipts this plugin actually signed and stored are **not** in
that shape.  Its AWR/1 issuer rendered

    modelId:claude-sonnet-4@anthropic|providerHub:https://hub.example.com|...

— ``key:value`` pairs, separator ``:``, **top level only**, with a nested object or array
rendered as a JSON-ish blob rather than flattened into leaf paths.  Under §12.1's rules
the same subject renders as

    inputHash.algorithm=SHA-256|inputHash.value=1447…|modelId=…

so the two forms produce different bytes for every receipt, and the reference AWR/1
verifier reports ``AWR-LEGACY-002`` for a document this plugin issued and signed
correctly.  That is verified, not assumed: see ``tests/test_legacy_verification.py``.

Deleting this rendering and delegating to ``awr.legacy`` alone would therefore mean every
stored receipt in every deployed hub becomes unverifiable — the one thing a provenance
layer must never do.  It is kept, named ``hub-1``, and tried **in addition to** the two
dialects §12.1 defines, so that a foreign AWR/1 document still verifies through the
reference implementation.  The divergence is reported upstream as a spec finding: §12.1
is presented as descriptive of AWR/1 and does not describe the AWR/1 that exists.

Nothing in this module is used for AWR/2.  AWR/2 canonicalization is RFC 8785 in the
``awr`` package and nowhere else (see :mod:`aimarket_provenance._awr`).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import unicodedata
from typing import Any

from ._awr import LEGACY_PROOF_TYPE, Reasons, awr, legacy_canonical_form

__all__ = [
    "DIALECT_NATIVE",
    "HASH_ALGORITHM",
    "LEGACY_PROOF_TYPE",
    "LEGACY_UNSIGNED_FIELDS",
    "compute_hash",
    "credential_subject_canonical",
    "is_legacy_document",
    "json_canonical",
    "legacy_public_key",
    "legacy_renderings",
    "public_key_from_jwk",
    "public_key_to_jwk",
    "verify_legacy_receipt",
]

#: This plugin's own AWR/1 rendering, tried before the two §12.1 dialects.
DIALECT_NATIVE = "hub-1"

#: SPEC.md §12: the fields AWR/1 left outside its signature.  A verifier MUST NOT report
#: them as attested — ``id`` outside the signature is what made AWR/1 chain edges
#: forgeable (§13.1), and it is the defect AWR/2 exists to close.
LEGACY_UNSIGNED_FIELDS = ("id", "type", "issuer", "hubInfo", "issuanceDate")

#: The digest algorithm label AWR/1 wrote into ``inputHash``/``outputHash``.
HASH_ALGORITHM = "SHA-256"


# ── the AWR/1 "JCS" rendering, as this plugin's issuer wrote it ────────────────
#
# Kept verbatim from the pre-migration `receipt.py` so that the bytes it produces are
# bit-identical to the bytes stored receipts were signed over.  It is NOT RFC 8785 and
# the docstring no longer claims to be: it applies NFC normalization (RFC 8785 §3.1
# forbids that), sorts keys by code point rather than by UTF-16 code unit (§3.2.3), and
# renders a whole-valued float as `<int>.0` and every other float to ten decimal places
# — the "JCS-labelled variant" of Appendix D. Do not fix it. Changing a byte here
# invalidates history.


def json_canonical(obj: Any) -> str:
    """The AWR/1 hash preimage serialization. **Not** RFC 8785 — see module docstring."""
    if isinstance(obj, dict):
        inner = ",".join(
            f"{json_canonical(k)}:{json_canonical(v)}"
            for k, v in sorted(obj.items(), key=lambda x: x[0])
        )
        return "{" + inner + "}"
    if isinstance(obj, (list, tuple)):
        inner = ",".join(json_canonical(i) for i in obj)
        return "[" + inner + "]"
    if isinstance(obj, str):
        normalized = unicodedata.normalize("NFC", obj)
        return json.dumps(normalized, ensure_ascii=False)
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if obj is None:
        return "null"
    if isinstance(obj, (int, float)):
        if isinstance(obj, float):
            if obj == int(obj):
                return f"{int(obj)}.0"
            return f"{obj:.10f}".rstrip("0").rstrip(".")
        return str(obj)
    return json.dumps(obj, ensure_ascii=False)


def compute_hash(data: dict[str, Any]) -> str:
    """SHA-256 hex over the AWR/1 preimage of *data*.

    This is the AWR/1 payload-digest rule; AWR/2 receipts carry an SRI digest over
    ``awr``'s RFC 8785 bytes instead (see ``receipt.payload_digest``).  Kept so that the
    ``input_hash``/``output_hash`` of a stored AWR/1 row can still be reproduced from its
    payload.
    """
    return hashlib.sha256(json_canonical(data).encode("utf-8")).hexdigest()


def credential_subject_canonical(subject: dict[str, Any]) -> str:
    """The ``hub-1`` AWR/1 canonical form: ``key:value|key:value``, top level only."""
    parts: list[str] = []
    for key in sorted(subject.keys()):
        val = subject[key]
        if isinstance(val, dict):
            val = json_canonical(val)
        elif isinstance(val, list):
            val = json_canonical(val)
        elif isinstance(val, float):
            if val == int(val):
                val = f"{int(val)}.0"
            else:
                val = f"{val:.10f}".rstrip("0").rstrip(".")
        parts.append(f"{key}:{val}")
    return "|".join(parts)


# ── JWK helpers (AWR/1 embedded its public key this way) ──────────────────────


def public_key_to_jwk(public_key_b64: str) -> dict[str, str]:
    """Convert a base64 Ed25519 public key to an RFC 8037 OKP JWK."""
    raw = base64.b64decode(public_key_b64)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"kty": "OKP", "crv": "Ed25519", "x": x}


def public_key_from_jwk(jwk: dict[str, str]) -> str:
    """Convert an RFC 8037 OKP JWK back to a base64 Ed25519 public key."""
    x = jwk.get("x", "")
    padding = 4 - len(x) % 4
    if padding != 4:
        x += "=" * padding
    raw = base64.urlsafe_b64decode(x)
    return base64.b64encode(raw).decode()


# ── verification ──────────────────────────────────────────────────────────────


def is_legacy_document(document: Any) -> bool:
    """True when *document* carries an AWR/1 ``Ed25519Signature2018`` proof."""
    return bool(awr.is_legacy_document(document))


def _proof_of(document: dict[str, Any]) -> Any:
    proof = document.get("proof")
    if isinstance(proof, list):
        return proof[0] if proof else None
    return proof


def _decode_legacy_signature(value: Any) -> bytes:
    """AWR/1 ``proofValue`` is base64 (SPEC.md §12.2). Raises ValueError otherwise.

    Decoding is attempted **before** any prefix heuristic, and that is not a stylistic
    choice.  ``z`` is a perfectly ordinary first character of a base64 string — it encodes
    the six bits ``110011``, so roughly one AWR/1 signature in 64 starts with it — while
    ``z`` is also the multibase tag AWR/2 requires (§6.1).  A verifier that reads the
    prefix first therefore refuses about 1.6% of the AWR/1 receipts ever issued, with a
    message saying they are AWR/2 documents.  The document has already been identified as
    AWR/1 by its ``Ed25519Signature2018`` proof type, so base64 is simply the encoding
    here, and the AWR/2 hint is only worth giving when the value does not decode at all.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("legacy proofValue must be a non-empty string")
    padded = value + "=" * (-len(value) % 4)
    try:
        signature = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError) as exc:
        if value.startswith("z"):
            raise ValueError(
                "legacy proofValue is not base64 (%s); a 'z'-prefixed value that does "
                "not decode as base64 is the multibase base58btc form AWR/2 requires "
                "(SPEC.md §6.1), which an AWR/1 document must not carry" % (exc,)
            )
        raise ValueError("legacy proofValue is not base64: %s" % (exc,))
    if len(signature) != 64:
        raise ValueError(
            "legacy proofValue decodes to %d bytes, expected 64" % (len(signature),)
        )
    return signature


def legacy_renderings(subject: dict[str, Any]) -> list[tuple[str, bytes]]:
    """Every AWR/1 canonical form a stored receipt may have been signed over.

    ``hub-1`` first because it is the one this plugin's issuer produced; then §12.1
    dialect A (integer-preserving) and B (float-coercing), which SPEC.md §12 requires an
    AWR/1-supporting verifier to try and accept either of.
    """
    renderings: list[tuple[str, bytes]] = [
        (DIALECT_NATIVE, credential_subject_canonical(subject).encode("utf-8"))
    ]
    for dialect in awr.LEGACY_DIALECTS:
        try:
            renderings.append((dialect, legacy_canonical_form(subject, dialect)))
        except TypeError:
            # §12.1: the rendering is undefined for numbers at or above 10^15, and a
            # verifier must report AWR-LEGACY-002 rather than choose one.  Dropping the
            # candidate here produces exactly that outcome below.
            continue
    return renderings


def legacy_public_key(document: dict[str, Any]) -> bytes | None:
    """The AWR/1 signing key (SPEC.md §12.2), from the document's embedded copy.

    AWR/1's ``issuer.id`` was ``did:key:`` followed by the first 32 characters of the
    base64 public key (Appendix D) — a string that is not a DID and names no recoverable
    key — so the key is taken from ``issuer.publicKeyJwk``, then
    ``issuer.publicKeyBase64``, then ``issuer.id`` when it happens to be a real
    ``did:key``.  Delegated to ``awr.legacy`` so both implementations look in the same
    places in the same order.
    """
    return awr.legacy.legacy_public_key(document)


def verify_legacy_receipt(
    document: dict[str, Any], reasons: Any = None
) -> dict[str, Any]:
    """Verify a stored AWR/1 document; return an SPEC.md §11.1-shaped result.

    ``AWR-LEGACY-001`` is always reported (§12), ``awrVersion`` is ``null`` because an
    AWR/1 document carries none (§11.1), ``profile`` is ``null`` because the profiles of
    §10 are defined over AWR/2 documents, and ``verifiedProof`` is ``null`` because an
    AWR/1 signature is not a §6.1 proof.  ``unsignedFields`` names what the signature did
    not cover, so a caller cannot mistake a received ``id`` for an attested one.
    """
    reasons = reasons if reasons is not None else Reasons()
    result: dict[str, Any] = {
        "valid": False,
        "awrVersion": None,
        "documentType": None,
        "profile": None,
        "reasons": [],
        "warnings": [],
        "chain": {"resolved": 0, "unresolved": 0},
        "verifiedProof": None,
        "legacy": True,
        "legacyDialect": None,
        "unsignedFields": [],
    }

    reasons.add(
        "AWR-LEGACY-001",
        "verified under the AWR/1 legacy rules (SPEC.md §12): id, type, issuer, "
        "issuanceDate and hubInfo are NOT covered by this signature and MUST NOT be "
        "reported as attested",
    )
    result["unsignedFields"] = [f for f in LEGACY_UNSIGNED_FIELDS if f in document]
    # §11.1: `type` is outside the AWR/1 signature, so this is what was received, not
    # what was signed — which is why `unsignedFields` lists it.
    result["documentType"] = awr.documents.document_type_of(document)

    subject = document.get("credentialSubject")
    if not isinstance(subject, dict):
        reasons.add("AWR-DOC-008", "credentialSubject must be a single object")
        return _finish(result, reasons)

    proof = _proof_of(document)
    if not isinstance(proof, dict):
        reasons.add("AWR-LEGACY-002", "AWR/1 document has no proof object")
        return _finish(result, reasons)

    public_key = legacy_public_key(document)
    if public_key is None:
        reasons.add(
            "AWR-KEY-001",
            "AWR/1 document carries no usable public key: issuer.publicKeyJwk, "
            "issuer.publicKeyBase64 or a real did:key issuer.id is required (§12.2)",
        )
        return _finish(result, reasons)

    try:
        signature = _decode_legacy_signature(proof.get("proofValue"))
    except ValueError as exc:
        # §12.2: a proofValue that is not base64, or not 64 bytes, is AWR-PROOF-005 —
        # not AWR-LEGACY-002, which means specifically that both dialects were tried
        # against a usable key and signature and both failed.
        reasons.add("AWR-PROOF-005", str(exc))
        return _finish(result, reasons)

    for dialect, message in legacy_renderings(subject):
        if awr.didkey.verify_signature(public_key, signature, message):
            result["legacyDialect"] = dialect
            return _finish(result, reasons, verified=True)

    reasons.add(
        "AWR-LEGACY-002",
        "signature verified under none of: this hub's native AWR/1 rendering (%s), "
        "SPEC.md §12.1 dialect A (integer-preserving), dialect B (float-coercing)"
        % (DIALECT_NATIVE,),
    )
    return _finish(result, reasons)


def _finish(
    result: dict[str, Any], reasons: Any, verified: bool = False
) -> dict[str, Any]:
    result["reasons"] = reasons.errors
    result["warnings"] = reasons.warnings
    result["valid"] = bool(verified) and not reasons.has_errors()
    return result
