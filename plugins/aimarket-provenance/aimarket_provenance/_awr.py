"""The AWR/2 dependency, imported loudly.

This plugin issues and verifies AWR/2 documents (``awr/SPEC.md`` 2.0.0).  It does **not**
implement RFC 8785 canonicalization (SPEC.md §4), the ``eddsa-jcs-2022`` Data Integrity
proof (§6) or ``did:key`` derivation (§5): all three live in the ``awr`` package and
nowhere else in this repository.

That is a deliberate constraint rather than a convenience.  AWR/1 shipped with a
canonicalizer in the issuer and a second one in the verifier, and the two disagreed on how
a JSON integer renders — ``2340`` against ``2340.0`` — which split one format into two
incompatible dialects that no signature could bridge (SPEC.md §4.3 and Appendix D).  A
local re-implementation here, however small, is how that happens again.

Consequently the import below **fails hard**.  There is no vendored fallback, no
"degraded mode" that signs with the plugin's old pipe-delimited rendering, and no
try/except that lets the hub boot with provenance quietly disabled: a receipt signed by a
second canonicalizer is worse than no receipt, because it verifies for its author and for
nobody else.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

_INSTALL_HINT = (
    "aimarket-provenance requires the AWR/2 reference implementation. Install it with\n"
    "    pip install awr>=2.0,<3\n"
    "or, from a checkout of this monorepo,\n"
    "    pip install -e awr/reference/python\n"
    "The plugin deliberately has no fallback: SPEC.md §4.3 and Appendix D record what a "
    "second canonicalizer did to AWR/1."
)


class ProvenanceDependencyError(ImportError):
    """The ``awr`` package is absent, or is not an AWR/2 implementation."""


#: Names the plugin actually uses.  Checked up front so that a same-named package that is
#: not AWR/2 fails here, with an actionable message, rather than as an AttributeError three
#: frames into a signing operation.
REQUIRED_NAMES = (
    "AWR_VERSION",
    "SigningKey",
    "issue_work_receipt",
    "verify_document",
    "canonical_sri",
    "document_reference",
    "canonicalize",
    "legacy_canonical_form",
    "Reasons",
)


def require_awr2(module: Any) -> Any:
    """Return *module* if it is an AWR/2 implementation; raise otherwise.

    A same-named package that is not AWR/2 is as dangerous as no package at all: it would
    be asked to sign documents whose rules it does not implement.  ``awr`` at the root of
    this monorepo is a directory of specs and vectors with no ``__init__.py``, so an
    accidental namespace-package import lands here too.
    """
    missing = [name for name in REQUIRED_NAMES if not hasattr(module, name)]
    if missing:
        raise ProvenanceDependencyError(
            "the imported 'awr' module (%s) is not the AWR/2 reference implementation: "
            "it is missing %s.\n\n%s"
            % (
                getattr(module, "__file__", None) or "<namespace package>",
                ", ".join(missing),
                _INSTALL_HINT,
            )
        )
    if not str(module.AWR_VERSION).startswith("2."):
        raise ProvenanceDependencyError(
            "the installed awr package implements AWR %s; this plugin issues AWR/2 "
            "documents and a major version it does not implement MUST be rejected rather "
            "than guessed at (SPEC.md §3.1, AWR-DOC-009).\n\n%s"
            % (module.AWR_VERSION, _INSTALL_HINT)
        )
    return module


try:  # noqa: SIM105 - the message is the point of the except clause
    import awr as _awr
except ImportError as exc:  # pragma: no cover - exercised by a broken install only
    raise ProvenanceDependencyError(
        "cannot import the 'awr' package: %s\n\n%s" % (exc, _INSTALL_HINT)
    ) from exc

awr = require_awr2(_awr)

# Re-exported so that the rest of the plugin never reaches for a crypto primitive of its
# own.  Everything here is `awr`'s.
AWR_CONTEXT = _awr.AWR_CONTEXT
AWR_VERSION = _awr.AWR_VERSION
CRYPTOSUITE = _awr.CRYPTOSUITE
EMPTY_PAYLOAD_SRI = _awr.EMPTY_PAYLOAD_SRI
LEGACY_PROOF_TYPE = _awr.LEGACY_PROOF_TYPE
PROOF_TYPE = _awr.PROOF_TYPE
Reasons = _awr.Reasons
SigningKey = _awr.SigningKey
TYPE_WORK_RECEIPT = _awr.TYPE_WORK_RECEIPT
VC_CONTEXT = _awr.VC_CONTEXT
WORK_STATUSES = _awr.WORK_STATUSES
canonical_sri = _awr.canonical_sri
canonicalize = _awr.canonicalize
derive_did_key = _awr.derive_did_key
document_reference = _awr.document_reference
is_legacy_document = _awr.is_legacy_document
issue_work_receipt = _awr.issue_work_receipt
legacy_canonical_form = _awr.legacy_canonical_form
make_bundle = _awr.make_bundle
parse_did_key = _awr.parse_did_key
verify_document = _awr.verify_document


class ProvenanceKeyError(RuntimeError):
    """The hub's signing key could not be turned into an AWR/2 ``did:key`` identity."""


_SIGNING_KEY_CACHE: dict = {}


def signing_key_from_signer(signer: Any) -> Any:
    """Build the ``awr.SigningKey`` for ``aimarket_hub.signing.Signer`` *signer*.

    The hub and its provenance receipts sign with **one** key: AWR/2 identity is the
    ``did:key`` derived from that key's public half (SPEC.md §5.1), so a second keypair
    here would give the same hub two unrelated identities and make every chain edge and
    every issuer-binding check meaningless.

    ``Signer`` keeps its seed private, but its on-disk format is fixed at 64 bytes,
    ``seed || publicKey`` (``aimarket_hub.signing._ensure_keypair``).  The seed is read
    from there and the derived public key is compared against ``signer.public_key_b64``:
    a mismatch means the file and the live signer disagree, which would be silently
    signing with the wrong key, so it raises instead.
    """
    key_path = getattr(signer, "key_path", None)
    public_key_b64 = getattr(signer, "public_key_b64", None)
    if key_path is None or not isinstance(public_key_b64, str):
        raise ProvenanceKeyError(
            "expected an aimarket_hub.signing.Signer with .key_path and "
            ".public_key_b64, got %r" % (type(signer).__name__,)
        )

    cached = _SIGNING_KEY_CACHE.get((str(key_path), public_key_b64))
    if cached is not None:
        return cached

    try:
        raw = Path(key_path).read_bytes()
    except OSError as exc:
        raise ProvenanceKeyError(
            "cannot read the hub signing key at %s: %s" % (key_path, exc)
        ) from exc
    if len(raw) != 64:
        raise ProvenanceKeyError(
            "the hub signing key at %s is %d bytes; the expected format is 64 bytes of "
            "seed || publicKey" % (key_path, len(raw))
        )

    key = SigningKey.from_seed(raw[:32])
    try:
        expected = base64.b64decode(public_key_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ProvenanceKeyError(
            "signer.public_key_b64 is not base64: %s" % (exc,)
        ) from exc
    if key.public_key_bytes != expected:
        raise ProvenanceKeyError(
            "the key file %s does not hold the key this Signer is using: the seed derives "
            "%s while the signer reports %s. Refusing to issue receipts under a did:key "
            "that names a different key than the hub signs with."
            % (
                key_path,
                base64.b64encode(key.public_key_bytes).decode(),
                public_key_b64,
            )
        )

    _SIGNING_KEY_CACHE[(str(key_path), public_key_b64)] = key
    return key


def did_key_for_signer(signer: Any) -> str:
    """The ``did:key`` (SPEC.md §5.1) this hub issues under."""
    return signing_key_from_signer(signer).did


def public_key_b64_from_did(did: str) -> str:
    """Standard-base64 raw public key named by *did*, for the legacy storage column."""
    return base64.b64encode(parse_did_key(did)).decode()
