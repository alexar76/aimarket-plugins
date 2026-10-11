"""ProvenanceReceipt — the hub's AWR/2 ``WorkReceipt`` (``awr/SPEC.md`` 2.0.0).

What changed, and why
---------------------

This plugin used to issue AWR/1: an ``Ed25519Signature2018`` proof whose signature covered
``credentialSubject`` **only**, over a pipe-delimited rendering that a docstring called
RFC 8785 and that was not, under a ``verificationMethod`` built as ``"did:key:"`` plus the
first 32 characters of a base64 public key.  Each of those is a defect with a consequence
(SPEC.md Appendix D):

* ``id``, ``type`` and ``issuer`` were outside the signature.  ``parents`` referenced
  receipts by ``id``, so an intermediary could rename a valid receipt and re-point a chain
  at it without breaking any signature (§13.1).  AWR/2 signs the whole document and makes
  chain edges content-addressed digest references (§3.2, §8.1).
* the canonicalizer applied NFC normalization, sorted keys by code point and rendered a
  whole-valued float as ``<int>.0``.  All three deviate from RFC 8785, which is how one
  format became two dialects that could not read each other's receipts (§4.1, §4.3).
  AWR/2 canonicalization lives in the ``awr`` package; this module does none of it.
* ``did:key:`` + 32 base64 characters is not a DID and names no key, so nothing in the
  document identified what to verify with (§5.1).  AWR/2's ``issuer.id`` is a real
  ``did:key`` and the public key is *derived from it*, which is why a forged receipt
  carrying its own keypair now announces itself: it has a different issuer.

Issuance is AWR/2 only.  SPEC.md §12 requires an implementation never to issue AWR/1, so
there is no parameter, flag or environment variable here that produces one; the AWR/1 code
path is verification of stored documents and lives in :mod:`aimarket_provenance.legacy`.

Shape of this class
-------------------

``ProvenanceReceipt`` is a **lossless wrapper over the AWR document**, not a typed struct
the document is rebuilt from.  SPEC.md §4.2 forbids re-serializing through a lossy
intermediate representation — a struct that drops unknown fields, a map that coerces
integers to floats — because the canonical bytes then differ from the ones the issuer
signed.  The document dict is therefore authoritative and every legacy attribute
(``model_id``, ``input_hash``, ``price_usd``, …) is a read-only view over it.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from ._awr import (
    AWR_CONTEXT,
    AWR_VERSION,
    EMPTY_PAYLOAD_SRI,
    VC_CONTEXT,
    awr,
    canonical_sri,
    document_reference,
    issue_work_receipt,
    public_key_b64_from_did,
    signing_key_from_signer,
)
from .legacy import (  # noqa: F401 — re-exported for callers of the pre-migration API
    HASH_ALGORITHM,
    compute_hash,
    credential_subject_canonical,
    is_legacy_document,
    json_canonical,
    public_key_from_jwk,
    public_key_to_jwk,
)

# ── Constants ──────────────────────────────────────────────────────

#: SPEC.md §6: AWR/2 registers exactly one cryptosuite.
PROVENANCE_PROOF_TYPE = "DataIntegrityProof"
PROVENANCE_CRYPTOSUITE = "eddsa-jcs-2022"

#: SPEC.md §3.1: first element exactly the VC 2.0 context, and the AWR namespace present.
#: A verifier MUST NOT dereference either (§13.5), so neither needs to resolve.
PROVENANCE_CONTEXT = [VC_CONTEXT, AWR_CONTEXT]

#: The hub's own label, carried alongside the one AWR type §3.1 permits.
PROVENANCE_TYPE = "AIProvenanceReceipt"

#: How ``inputDigest``/``outputDigest`` were produced (SPEC.md §3.3: "the issuer chooses
#: the payload serialization and SHOULD document it").  Recorded *inside* the signed
#: subject so a third party can reproduce the digest from the payload without guessing.
PAYLOAD_SERIALIZATION_JCS = "jcs-awr2"
PAYLOAD_SERIALIZATION_JSON = "json-sorted-compact"

#: SPEC.md §3.3 work statuses.  A receipt for work that did not succeed is a first-class
#: document, so this is a parameter and not a constant.
DEFAULT_STATUS = "succeeded"


def _rfc3339(moment: _dt.datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_dt.timezone.utc)
    return moment.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decimal_string(value: Any) -> str:
    """Render *value* as an SPEC.md §4.3 decimal string.

    Every quantity that is not a whole count is carried as a **string** in AWR/2, because
    a JSON float is the ambiguity that split AWR/1 (§4.3).  ``0.15`` therefore becomes
    ``"0.15"``, never ``0.15``.  A non-finite value has no decimal form and raises rather
    than being written as ``NaN``, which is not JSON at all.
    """
    if isinstance(value, str):
        if awr.documents.is_decimal_amount(value):
            return value
        raise ValueError(
            "%r is not a decimal string matching ^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$" % (value,)
        )
    if isinstance(value, bool):
        raise ValueError("a boolean is not a decimal amount")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("%r has no decimal representation" % (value,))
        try:
            # repr() is the shortest string that round-trips the double, so the decimal
            # string names the same value the caller passed.
            return format(Decimal(repr(value)), "f")
        except InvalidOperation as exc:  # pragma: no cover - guarded by the checks above
            raise ValueError("%r has no decimal representation: %s" % (value, exc))
    if isinstance(value, Decimal):
        return format(value, "f")
    raise ValueError("cannot render %r as a decimal string" % (value,))


def payload_digest(payload: Any, serialization: str | None = None) -> tuple[str, str]:
    """``(digestSRI, serialization)`` for an application payload (SPEC.md §3.3).

    §3.3 says the digest is over "application payload bytes, not of AWR documents", that
    the issuer chooses the serialization, and that for a JSON payload "the canonical form
    of §4 SHOULD be used so that an independent party can reproduce the digest".

    That recommendation cannot always be followed, and the plugin does not pretend
    otherwise.  §4 is RFC 8785 *as profiled by §4.3*, which forbids non-integer JSON
    numbers — and an application payload routinely carries one (``{"temperature": 0.7}``).
    So there is no §4 canonical form for such a payload, and §3.3 names no fallback.  This
    issuer therefore:

    * uses the §4 canonical bytes whenever they exist, reporting ``jcs-awr2``;
    * otherwise serializes with ``json.dumps(payload, sort_keys=True,
      separators=(",", ":"), ensure_ascii=False)`` encoded UTF-8, reporting
      ``json-sorted-compact``.

    The chosen name is written into the signed subject as ``payloadSerialization``, so the
    receipt states which rule reproduces its digests instead of leaving a reader to try
    both.  The gap is reported upstream as a spec finding.

    Pass *serialization* to force one of the two, which is what an issuer does when the
    other payload of the same receipt needed the fallback.  Note that the two rules
    coincide for most payloads — they differ only where §4 is stricter or where RFC 8785
    and Python disagree (a non-integer number, an astral object key) — so an equal digest
    is not evidence that the same rule was applied.
    """
    if serialization == PAYLOAD_SERIALIZATION_JSON:
        return _json_sorted_compact_sri(payload), PAYLOAD_SERIALIZATION_JSON
    if serialization not in (None, PAYLOAD_SERIALIZATION_JCS):
        raise ValueError("unknown payload serialization %r" % (serialization,))
    try:
        return canonical_sri(payload), PAYLOAD_SERIALIZATION_JCS
    except awr.AwrError:
        if serialization == PAYLOAD_SERIALIZATION_JCS:
            raise
        return _json_sorted_compact_sri(payload), PAYLOAD_SERIALIZATION_JSON


def _json_sorted_compact_sri(payload: Any) -> str:
    """The named fallback serialization of :func:`payload_digest`, as an SRI digest."""
    raw = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return awr.sri_encode(hashlib.sha256(raw).digest())


def _digest_value(value: Any) -> str:
    """Read a digest that may be an AWR/2 SRI string or an AWR/1 ``{algorithm, value}``."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        inner = value.get("value")
        return inner if isinstance(inner, str) else ""
    return ""


class ProvenanceReceipt:
    """One AWR/2 ``WorkReceipt``, or one stored AWR/1 document being read back.

    Construct through :meth:`create` (issues and signs) or :meth:`from_dict` /
    :meth:`from_json` (wraps an existing document).  The wrapped document is authoritative;
    the attributes below are views over it (SPEC.md §4.2).
    """

    __slots__ = ("_document", "_source_bytes")

    def __init__(
        self, document: dict[str, Any], source_bytes: bytes | None = None
    ) -> None:
        if not isinstance(document, dict):
            raise TypeError(f"Expected dict, got {type(document).__name__}")
        self._document = document
        self._source_bytes = source_bytes

    # ── Factory ─────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        model_id: str,
        provider_hub: str,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        signer: Any,
        hub_name: str = "",
        hub_version: str = "",
        parent_receipts: list[Any] | None = None,
        tee_attestation: dict[str, Any] | None = None,
        zk_input_proof: dict[str, Any] | None = None,
        zk_output_proof: dict[str, Any] | None = None,
        latency_ms: int = 0,
        price_usd: Any = 0.0,
        currency: str = "USD",
        invocation_nonce: str | None = None,
        reputation_score: Any = None,
        status: str = DEFAULT_STATUS,
        settlement: dict[str, Any] | None = None,
        receipt_id: str | None = None,
        now: _dt.datetime | None = None,
    ) -> "ProvenanceReceipt":
        """Issue and sign an AWR/2 ``WorkReceipt``.

        ``parent_receipts`` entries are digest references (SPEC.md §3.2): either a mapping
        already carrying ``digestSRI``, or a :class:`ProvenanceReceipt` / raw document to
        take the digest of.  A bare identifier string is **refused** — AWR/1 accepted one
        and that is precisely the forgeable edge of §13.1.  Callers holding only an id
        resolve it against storage first (see ``api.py``).
        """
        if status not in awr.WORK_STATUSES:
            raise ValueError(
                "work.status must be one of %s, got %r"
                % (", ".join(awr.WORK_STATUSES), status)
            )

        key = signing_key_from_signer(signer)
        completed = now or _dt.datetime.now(tz=_dt.timezone.utc)
        completed_at = _rfc3339(completed)

        digests = [payload_digest(input_payload), payload_digest(output_payload)]
        # One rule per receipt: if either payload has no §4 canonical form, both digests
        # are produced by the named fallback, so `payloadSerialization` is unambiguous.
        if any(kind == PAYLOAD_SERIALIZATION_JSON for _, kind in digests):
            serialization = PAYLOAD_SERIALIZATION_JSON
            input_sri, _ = payload_digest(input_payload, serialization)
            output_sri, _ = payload_digest(output_payload, serialization)
        else:
            serialization = PAYLOAD_SERIALIZATION_JCS
            input_sri, output_sri = digests[0][0], digests[1][0]

        work: dict[str, Any] = {
            "modelId": model_id,
            "completedAt": completed_at,
            "status": status,
        }
        latency = int(latency_ms or 0)
        if latency > 0:
            work["latencyMs"] = latency
            started = completed - _dt.timedelta(milliseconds=latency)
            work["startedAt"] = _rfc3339(started)

        subject: dict[str, Any] = {
            "work": work,
            "inputDigest": input_sri,
            "outputDigest": output_sri,
            # Hub-specific members.  SPEC.md §3.1 permits unknown properties at any level
            # and requires them to be canonicalized, so unlike AWR/1's unsigned `issuer`
            # these are covered by the signature.
            "providerHub": provider_hub,
            "payloadSerialization": serialization,
        }

        parents = _normalize_parents(parent_receipts)
        if parents:
            subject["parents"] = parents

        amount = decimal_string(price_usd if price_usd is not None else 0)
        # Omitted only when the price is exactly zero. `> 0` would silently drop a
        # negative amount (a credit or refund), which §3.3's grammar explicitly permits.
        if Decimal(amount) != 0:
            subject["price"] = {"currency": currency, "amount": amount}

        nonce = invocation_nonce or str(uuid.uuid4())
        if nonce:
            subject["nonce"] = nonce

        environment: dict[str, Any] = {}
        if tee_attestation:
            environment["teeAttestation"] = tee_attestation
        zk: dict[str, Any] = {}
        if zk_input_proof:
            zk["input"] = zk_input_proof
        if zk_output_proof:
            zk["output"] = zk_output_proof
        if zk:
            # §7.3: the member is opaque to AWR/2, so its internal shape is the issuer's.
            # Both AWR/1 members are carried under the spec-named `zkProof` so that a
            # verifier's AWR-ENV-001 warning actually fires for them.
            environment["zkProof"] = zk
        if environment:
            subject["environment"] = environment

        if reputation_score is not None:
            # Not an AWR field. §4.3 forbids a non-integer JSON number anywhere inside a
            # signed document, so the score is a decimal string like every other fraction.
            subject["reputationScore"] = decimal_string(reputation_score)

        if settlement:
            subject["settlement"] = settlement

        hub_info = {
            "hubName": hub_name or provider_hub,
            "hubVersion": hub_version,
            "protocolVersion": "v2",
        }

        document = issue_work_receipt(
            subject,
            key,
            document_id=receipt_id or ("urn:uuid:%s" % (uuid.uuid4(),)),
            valid_from=completed_at,
            created=completed_at,
            issuer_name=hub_name or provider_hub or None,
            extra_types=[PROVENANCE_TYPE],
            # Top level and therefore signed. In AWR/1 `hubInfo` sat outside the
            # signature, so a relay could rewrite the hub's own name and version.
            extra_properties={"hubInfo": hub_info},
        )
        return cls(document)

    # ── Serialization ──────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """The AWR document, ready to serialize.

        A copy, so a caller mutating the result cannot silently invalidate the signature
        of the receipt this object holds.
        """
        return json.loads(json.dumps(self._document))

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self._document, **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProvenanceReceipt":
        """Wrap a parsed AWR document (AWR/2 or a stored AWR/1 one)."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")
        return cls(data)

    @classmethod
    def from_json(cls, text: Any) -> "ProvenanceReceipt":
        """Wrap a document from its **received bytes**.

        Preferred over :meth:`from_dict` for anything read out of storage or off the wire:
        SPEC.md §4.3 makes the number check lexical and requires it to be applied to the
        received bytes, and §4.1 requires duplicate property names to be rejected.  Both
        are impossible once the JSON has been through a permissive parser, so the bytes are
        kept and handed to the verifier.

        A stored AWR/1 document is read on the second path SPEC.md §12 requires: "a
        verifier whose parser enforces §4.3 lexically MUST re-read the bytes with the
        number restriction lifted when the strict parse failed only on a number, and
        continue on this path if what comes out is an AWR/1 document".  Without it every
        stored AWR/1 receipt with a non-zero ``priceUsd`` — a JSON float, legal in AWR/1
        and forbidden in AWR/2 — would be unreadable, which is a data-loss bug dressed up
        as strictness.
        """
        raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        try:
            return cls(awr.loads(raw), source_bytes=raw)
        except awr.AwrError as err:
            if err.code not in ("AWR-CANON-001", "AWR-CANON-002"):
                raise
            lenient = awr.loads(raw, allow_non_integer_numbers=True)
            if not is_legacy_document(lenient):
                # An AWR/2 document containing a non-integer number is invalid, and
                # quietly accepting it here would sign or report bytes no conformant
                # issuer can have produced (§4.3).
                raise
            return cls(lenient, source_bytes=raw)

    # ── Verification ───────────────────────────────────────────

    @property
    def document(self) -> dict[str, Any]:
        """The wrapped document itself (not a copy) — the authoritative form."""
        return self._document

    @property
    def source_bytes(self) -> bytes | None:
        return self._source_bytes

    def verify_result(
        self, supporting: list[Any] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Full SPEC.md §11.1 result for this receipt.

        AWR/2 documents go to ``awr.verify_document``; a stored AWR/1 document goes to
        :func:`aimarket_provenance.legacy.verify_legacy_receipt`, which reports
        ``AWR-LEGACY-001`` and never grants a profile.

        *supporting* and the AWR/2 keyword arguments (``profile``, ``now``, ``max_depth``,
        …) do not apply on the legacy path and are not passed to it: SPEC.md §12 says an
        AWR/1 document reports ``AWR-LEGACY-001``, the outcome of the signature check, and
        nothing derived from the AWR/2 rules — which includes the §8 chain walk, the §10
        profiles and the §11.3 time warnings.
        """
        from .legacy import verify_legacy_receipt

        if self.is_legacy:
            return verify_legacy_receipt(self._document)
        target: Any = self._source_bytes if self._source_bytes is not None else self._document
        return awr.verify_document(target, supporting=supporting, **kwargs)

    def verify(self, signer: Any = None) -> bool:
        """True when the document verifies.

        The ``signer`` argument is accepted for source compatibility with the AWR/1 API and
        is **ignored**: AWR/2 derives the public key from ``issuer.id`` (SPEC.md §5.1), so
        a verifier never chooses a key, and passing one in could only be used to check a
        document against a key it does not name.
        """
        return bool(self.verify_result().get("valid"))

    # ── Views over the document ────────────────────────────────

    @property
    def is_legacy(self) -> bool:
        """True for a stored AWR/1 document (``Ed25519Signature2018``)."""
        return is_legacy_document(self._document)

    @property
    def awr_version(self) -> str | None:
        """The document's own ``awrVersion`` — ``None`` for AWR/1, which carries none."""
        version = self._document.get("awrVersion")
        return version if isinstance(version, str) else None

    def _subject(self) -> dict[str, Any]:
        subject = self._document.get("credentialSubject")
        return subject if isinstance(subject, dict) else {}

    def _work(self) -> dict[str, Any]:
        work = self._subject().get("work")
        return work if isinstance(work, dict) else {}

    def _proof(self) -> dict[str, Any]:
        proof = self._document.get("proof")
        if isinstance(proof, list):
            proof = proof[0] if proof else None
        return proof if isinstance(proof, dict) else {}

    def _issuer(self) -> dict[str, Any]:
        issuer = self._document.get("issuer")
        return issuer if isinstance(issuer, dict) else {}

    def _hub_info(self) -> dict[str, Any]:
        info = self._document.get("hubInfo")
        return info if isinstance(info, dict) else {}

    @property
    def receipt_id(self) -> str:
        value = self._document.get("id")
        return value if isinstance(value, str) else ""

    @property
    def context(self) -> list[str]:
        value = self._document.get("@context")
        return list(value) if isinstance(value, list) else []

    @property
    def type(self) -> list[str]:
        value = self._document.get("type")
        return list(value) if isinstance(value, list) else []

    @property
    def issuer_id(self) -> str:
        value = self._issuer().get("id")
        return value if isinstance(value, str) else ""

    #: SPEC.md §5.1 identity.  For AWR/2 this is a real ``did:key``; for a stored AWR/1
    #: document it is whatever that document put in ``issuer.id``, which Appendix D
    #: records as not being a DID at all — hence the separate name.
    @property
    def issuer_did(self) -> str:
        return "" if self.is_legacy else self.issuer_id

    @property
    def issuer_name(self) -> str:
        value = self._issuer().get("name")
        return value if isinstance(value, str) else ""

    @property
    def issuer_public_key_b64(self) -> str:
        """Standard-base64 raw Ed25519 public key of the issuer.

        For AWR/2 this is **derived from** ``issuer.id`` and is therefore not a separate
        assertion that could disagree with the DID (SPEC.md §5.1).  For AWR/1 it comes
        from the document's embedded ``publicKeyJwk``, which is all AWR/1 offered.
        """
        if not self.is_legacy:
            try:
                return public_key_b64_from_did(self.issuer_id)
            except Exception:
                return ""
        jwk = self._issuer().get("publicKeyJwk")
        if isinstance(jwk, dict) and jwk.get("x"):
            try:
                return public_key_from_jwk(jwk)
            except Exception:
                return ""
        return ""

    @property
    def model_id(self) -> str:
        if self.is_legacy:
            value = self._subject().get("modelId")
        else:
            value = self._work().get("modelId")
        return value if isinstance(value, str) else ""

    @property
    def status(self) -> str:
        value = self._work().get("status")
        return value if isinstance(value, str) else ""

    @property
    def provider_hub(self) -> str:
        value = self._subject().get("providerHub")
        if isinstance(value, str) and value:
            return value
        return self.issuer_name

    @property
    def timestamp(self) -> str:
        """The receipt's own time: ``validFrom`` in AWR/2, ``timestamp`` in AWR/1."""
        if self.is_legacy:
            value = self._subject().get("timestamp")
            return value if isinstance(value, str) else ""
        value = self._document.get("validFrom")
        return value if isinstance(value, str) else ""

    @property
    def completed_at(self) -> str:
        value = self._work().get("completedAt")
        return value if isinstance(value, str) else self.timestamp

    @property
    def input_digest_sri(self) -> str:
        return _digest_value(self._subject().get("inputDigest"))

    @property
    def output_digest_sri(self) -> str:
        return _digest_value(self._subject().get("outputDigest"))

    @property
    def input_hash(self) -> str:
        """The input digest **as the document states it**.

        AWR/2: an SRI string, ``sha256-<base64>`` (SPEC.md §3.2).  AWR/1: the bare 64-hex
        SHA-256 it carried in ``inputHash.value``.  The storage column keeps its name and
        its meaning; only the encoding of new rows changed (see ``storage.py``).
        """
        if self.is_legacy:
            return _digest_value(self._subject().get("inputHash"))
        return self.input_digest_sri

    @property
    def output_hash(self) -> str:
        if self.is_legacy:
            return _digest_value(self._subject().get("outputHash"))
        return self.output_digest_sri

    @property
    def payload_serialization(self) -> str:
        value = self._subject().get("payloadSerialization")
        return value if isinstance(value, str) else ""

    @property
    def parents(self) -> list[dict[str, Any]]:
        """AWR/2 ``parents``: content-addressed digest references (SPEC.md §3.2, §8.1)."""
        value = self._subject().get("parents")
        return [p for p in value if isinstance(p, dict)] if isinstance(value, list) else []

    @property
    def parent_receipts(self) -> list[str]:
        """Parent identifiers only — the AWR/1-shaped view, kept for the storage column.

        An identifier is *not* the edge: AWR/2 commits to the parent's exact bytes through
        ``digestSRI``, which is what stops a chain being re-pointed (§8.1).  Use
        :attr:`parents` for anything that must be sound.
        """
        if self.is_legacy:
            value = self._subject().get("parentReceipts")
            return [str(v) for v in value] if isinstance(value, list) else []
        out: list[str] = []
        for entry in self.parents:
            entry_id = entry.get("id")
            if isinstance(entry_id, str) and entry_id:
                out.append(entry_id)
        return out

    @property
    def latency_ms(self) -> int:
        value = self._work().get("latencyMs") if not self.is_legacy else self._subject().get("latencyMs")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return int(value)

    @property
    def price_amount(self) -> str:
        """``price.amount`` — a decimal **string** in AWR/2 (SPEC.md §3.3, §4.3)."""
        price = self._subject().get("price")
        if isinstance(price, dict) and isinstance(price.get("amount"), str):
            return price["amount"]
        if self.is_legacy:
            legacy = self._subject().get("priceUsd")
            if isinstance(legacy, (int, float)) and not isinstance(legacy, bool):
                return decimal_string(legacy)
        return "0"

    @property
    def price_currency(self) -> str:
        price = self._subject().get("price")
        if isinstance(price, dict) and isinstance(price.get("currency"), str):
            return price["currency"]
        return "USD" if self.is_legacy else ""

    @property
    def price_usd(self) -> float:
        """The price as a float, for the ``price_usd REAL`` storage column only.

        Never used to build or compare a document: SPEC.md §4.3 requires decimal
        comparison, "never by parsing to a binary float".  :attr:`price_amount` is the
        signed value.
        """
        try:
            return float(Decimal(self.price_amount))
        except (InvalidOperation, ValueError):
            return 0.0

    @property
    def invocation_nonce(self) -> str:
        if self.is_legacy:
            value = self._subject().get("invocationNonce")
        else:
            value = self._subject().get("nonce")
        return value if isinstance(value, str) else ""

    def _environment(self) -> dict[str, Any]:
        env = self._subject().get("environment")
        return env if isinstance(env, dict) else {}

    @property
    def tee_attestation(self) -> dict[str, Any] | None:
        value = self._environment().get("teeAttestation")
        if isinstance(value, dict):
            return value
        legacy = self._subject().get("teeAttestation")
        return legacy if isinstance(legacy, dict) else None

    def _zk(self) -> dict[str, Any]:
        value = self._environment().get("zkProof")
        return value if isinstance(value, dict) else {}

    @property
    def zk_input_proof(self) -> dict[str, Any] | None:
        value = self._zk().get("input")
        if isinstance(value, dict):
            return value
        legacy = self._subject().get("zkInputProof")
        return legacy if isinstance(legacy, dict) else None

    @property
    def zk_output_proof(self) -> dict[str, Any] | None:
        value = self._zk().get("output")
        if isinstance(value, dict):
            return value
        legacy = self._subject().get("zkOutputProof")
        return legacy if isinstance(legacy, dict) else None

    @property
    def reputation_score(self) -> float | None:
        value = self._subject().get("reputationScore")
        if isinstance(value, str):
            try:
                return float(Decimal(value))
            except (InvalidOperation, ValueError):
                return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    @property
    def settlement(self) -> dict[str, Any] | None:
        value = self._subject().get("settlement")
        return value if isinstance(value, dict) else None

    @property
    def proof_type(self) -> str:
        value = self._proof().get("type")
        return value if isinstance(value, str) else ""

    @property
    def cryptosuite(self) -> str:
        value = self._proof().get("cryptosuite")
        return value if isinstance(value, str) else ""

    @property
    def proof_created(self) -> str:
        value = self._proof().get("created")
        return value if isinstance(value, str) else ""

    @property
    def proof_verification_method(self) -> str:
        value = self._proof().get("verificationMethod")
        return value if isinstance(value, str) else ""

    @property
    def proof_value(self) -> str:
        value = self._proof().get("proofValue")
        return value if isinstance(value, str) else ""

    @property
    def hub_name(self) -> str:
        value = self._hub_info().get("hubName")
        if isinstance(value, str) and value:
            return value
        return self.issuer_name

    @property
    def hub_version(self) -> str:
        value = self._hub_info().get("hubVersion")
        return value if isinstance(value, str) else ""

    @property
    def protocol_version(self) -> str:
        value = self._hub_info().get("protocolVersion")
        return value if isinstance(value, str) else "v2"

    @property
    def digest_sri(self) -> str:
        """This document's own digest — what a child receipt's ``parents`` edge commits to.

        Over the **secured** document including its ``proof`` (SPEC.md §8.1).
        """
        return canonical_sri(self._document)

    def digest_reference(self) -> dict[str, str]:
        """A SPEC.md §3.2 digest reference to this receipt, for a child's ``parents``."""
        return document_reference(self._document)

    def __repr__(self) -> str:
        return "<ProvenanceReceipt %s %s %s>" % (
            self.receipt_id or "(no id)",
            "AWR/1" if self.is_legacy else "AWR/%s" % (self.awr_version,),
            self.model_id,
        )


def _normalize_parents(parent_receipts: Any) -> list[dict[str, Any]]:
    """Turn caller-supplied parents into SPEC.md §3.2 digest references.

    A bare identifier string is refused with a message that says why, rather than being
    turned into an ``id``-only edge: AWR/1 linked parents by identifier and that is the
    forgeable edge of §13.1 — the entire reason ``parents`` is content-addressed now.
    """
    if not parent_receipts:
        return []
    out: list[dict[str, Any]] = []
    for entry in parent_receipts:
        if isinstance(entry, ProvenanceReceipt):
            out.append(entry.digest_reference())
            continue
        if isinstance(entry, str):
            raise ValueError(
                "parent %r is an identifier, not a digest reference. AWR/2 chain edges "
                "commit to the parent's exact bytes (SPEC.md §3.2, §8.1); an id-only edge "
                "is the AWR/1 defect that let an intermediary re-point a chain (§13.1). "
                "Pass the parent document, a ProvenanceReceipt, or "
                "{'id': ..., 'digestSRI': 'sha256-...'}." % (entry,)
            )
        if isinstance(entry, dict) and isinstance(entry.get("digestSRI"), str):
            out.append(dict(entry))
            continue
        if isinstance(entry, dict):
            # A full AWR document: take its digest.
            out.append(document_reference(entry))
            continue
        raise ValueError(
            "cannot build a digest reference from %r (%s)"
            % (entry, type(entry).__name__)
        )
    return out


__all__ = [
    "AWR_VERSION",
    "DEFAULT_STATUS",
    "EMPTY_PAYLOAD_SRI",
    "HASH_ALGORITHM",
    "PAYLOAD_SERIALIZATION_JCS",
    "PAYLOAD_SERIALIZATION_JSON",
    "PROVENANCE_CONTEXT",
    "PROVENANCE_CRYPTOSUITE",
    "PROVENANCE_PROOF_TYPE",
    "PROVENANCE_TYPE",
    "ProvenanceReceipt",
    "compute_hash",
    "credential_subject_canonical",
    "decimal_string",
    "is_legacy_document",
    "json_canonical",
    "payload_digest",
    "public_key_from_jwk",
    "public_key_to_jwk",
]
