# aimarket-provenance — Receipt format (AWR/2)

Every receipt this plugin issues is an **AWR/2 `WorkReceipt`**: a W3C Verifiable Credential
2.0 carrying an `eddsa-jcs-2022` Data Integrity proof over RFC 8785 canonical bytes, issued
by a `did:key`. The normative definition is [`awr/SPEC.md`](https://github.com/alexar76/aicom/blob/main/awr/SPEC.md) 2.0.0;
this page documents only the choices this issuer makes inside it.

## Envelope

| Member | Value here | Spec |
|---|---|---|
| `@context` | `["https://www.w3.org/ns/credentials/v2", "https://verify.modelmarket.dev/ns/awr/v2"]` | §3.1 — a verifier MUST NOT dereference either |
| `id` | `urn:uuid:<v4>` | §3.1 — inside the signature, so it is a binding statement, not a hint |
| `type` | `["VerifiableCredential", "WorkReceipt", "AIProvenanceReceipt"]` | §3.1 — exactly one AWR type; `AIProvenanceReceipt` is this hub's own label and carries no weight |
| `issuer.id` | the hub's `did:key` | §5.1 — the public key is *derived from* this string |
| `issuer.name` | the hub name, informational | §3.1 — carries no trust weight |
| `validFrom` | RFC 3339 UTC, second precision | §3.1 |
| `awrVersion` | `"2.0.0"` | §3.1 — signed, so the document cannot be re-read under another version's rules |
| `hubInfo` | `{hubName, hubVersion, protocolVersion}` | §3.1 unknown property — top-level and **signed**; in AWR/1 it sat outside the signature |

`proof.verificationMethod` is `<did>#<method-specific-id>`, the `did:key` method's own
verification method identifier (§5.3), so a verifier never has to choose a key.

## `credentialSubject`

| Member | Type | Notes |
|---|---|---|
| `work.modelId` | string | `<capability>@<product>` for auto-receipts; an opaque label (§3.3) |
| `work.status` | enum | `succeeded` \| `failed` \| `refused` \| `timeout` \| `partial` |
| `work.completedAt` | RFC 3339 UTC | required |
| `work.startedAt` | RFC 3339 UTC | present when `latencyMs` is, computed as `completedAt − latencyMs` |
| `work.latencyMs` | integer | omitted when zero; never a float (§4.3) |
| `inputDigest`, `outputDigest` | SRI string | `sha256-<base64>` over the payload — see below |
| `parents` | digest references | `{id, digestSRI}`, committing to the parent's exact secured bytes (§8.1) |
| `price` | `{currency, amount}` | `amount` is a **decimal string**; omitted when zero |
| `nonce` | string | what makes two receipts over identical input distinguishable (§3.3) |
| `environment.teeAttestation` | opaque object | inside the signature, **not verified** (§7.3) |
| `environment.zkProof` | opaque object | `{input?, output?}` — this issuer's shape inside a spec-named opaque member |
| `settlement` | object | optional accountability binding (§10.3); presence yields `AWR-L2-001` |
| `providerHub` | string | hub-specific, **signed**. In AWR/1 the same fact lived in the unsigned `issuer.id` |
| `payloadSerialization` | string | which rule reproduces the two digests — see below |
| `reputationScore` | decimal string | hub-specific; a string because §4.3 forbids a non-integer JSON number anywhere in a signed document |

### No non-integer JSON numbers, anywhere

§4.3 restricts the **number literal**, not the value: `2340.0` is forbidden even though
`2340` is fine, and the check is lexical over the received bytes. Every quantity that is
not a whole count is therefore a decimal string in this receipt — `price.amount`,
`reputationScore` — and comparisons of them must be decimal arithmetic, never a float
parse. `2340` vs `2340.0` is the exact divergence that split AWR/1 into two dialects.

### Payload digests and `payloadSerialization`

`inputDigest` and `outputDigest` digest **application payload bytes**, not AWR documents,
and §3.3 leaves the payload serialization to the issuer while recommending the §4 canonical
form for JSON "so that an independent party can reproduce the digest".

That recommendation cannot always be followed, and this issuer does not pretend otherwise.
§4 is RFC 8785 *as profiled by §4.3*, which forbids non-integer JSON numbers — and an
invoke payload routinely contains one (`{"temperature": 0.7}`). Such a payload has no §4
canonical form at all, and §3.3 names no fallback. So:

| `payloadSerialization` | Rule |
|---|---|
| `jcs-awr2` | the §4 canonical bytes — RFC 8785 with the AWR number profile |
| `json-sorted-compact` | `json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` encoded UTF-8 |

The chosen name is written into the signed subject, so the receipt states which rule
reproduces its digests instead of leaving a reader to try both. One rule applies to both
digests of a receipt: if either payload lacks a §4 form, both use the fallback. The two
rules coincide for most payloads and diverge exactly where §4.3 bites (a non-integer
number) or where RFC 8785 and Python's `sorted` disagree (an object key outside the BMP),
so an equal digest is not evidence that the same rule was applied — which is why the member
is stated rather than inferred.

The gap in §3.3 is reported upstream as a spec finding.

## Verifying a receipt

Three ways, in increasing independence from this hub:

```bash
# 1. this hub's own verifier — SPEC.md §11.1 result plus the plugin's checks
curl -s "$HUB/ai-market/v2/p/provenance/verify/urn:uuid:…" | jq

# 2. the AWR/2 reference CLI, offline, against the document only
curl -s "$HUB/ai-market/v2/p/provenance/receipt/urn:uuid:…" > receipt.json
python -m awr verify receipt.json            # exit 0 = valid
python -m awr verify receipt.json --profile L1 --parents verdict.json

# 3. the browser verifier — paste receipt.json at https://verify.modelmarket.dev
```

All three answer the same question and none of them contacts a network during
verification: §13.5 forbids a verifier from dereferencing `@context` URIs, parent
documents, evidence or policies, and §1.2 makes offline verification the design floor.

A chain needs its parents supplied, because a verifier must not fetch them:

```bash
python -m awr verify child.json --parents parent.json
# → "chain": {"resolved": 1, "unresolved": 0}
```

An unresolved edge is **not** an error (§8.2). "Chain not checked" and "chain intact" are
different answers, and the `chain` counters are how you tell them apart.

## Reason codes

Verification results carry stable machine-readable codes (§11.2) in `reasons` (severity
`error`) and `warnings` (severity *warning*). A code has exactly one severity, so
`valid == (no error-severity reason)` always holds. The ones you will actually see from
this plugin:

| Code | Meaning here |
|---|---|
| `AWR-PROOF-006` | the signature was checked and did not verify — something in the document was changed |
| `AWR-PROOF-005` | `proofValue` is not multibase base58btc of 64 bytes, e.g. an AWR/1 base64 signature in an AWR/2 document |
| `AWR-CHAIN-003` | a supplied parent's bytes are not the ones the child committed to |
| `AWR-ENV-001` | *(warning)* an attestation is present and was not verified |
| `AWR-L2-001` | *(warning)* a `settlement`/`stake` binding is present; on-chain existence was not checked |
| `AWR-TIME-002` | *(warning)* `validUntil` is in the past. Age is policy, not validity (§11.3) |
| `AWR-LEGACY-001` | *(warning)* the document is AWR/1 and was verified under §12 |
| `AWR-LEGACY-002` | an AWR/1 document verified under none of the known renderings |

## AWR/1 receipts already in storage

Stored AWR/1 documents remain verifiable and are reported as legacy. What changes is what a
legacy result is allowed to claim:

- `AWR-LEGACY-001` is always reported;
- `unsignedFields` names `id`, `type`, `issuer`, `issuanceDate`, `hubInfo` — the AWR/1
  signature covered `credentialSubject` only, so none of them is attested (§12, §13.1);
- `awrVersion` is `null` (an AWR/1 document carries none) and `profile` is `null`
  (the §10 profiles are defined over AWR/2 documents);
- `verifiedProof` is `null`, because an AWR/1 signature is not a §6.1 proof;
- AWR/2 rules that postdate AWR/1 are **not** applied: the VC 1.1 context, the `priceUsd`
  JSON float and the fake `did:key` are all left alone, since reporting them would fail
  every AWR/1 document for reasons §12 does not state.

Field mapping, for reading old receipts:

| AWR/1 | AWR/2 |
|---|---|
| `credentialSubject.modelId` | `credentialSubject.work.modelId` |
| `credentialSubject.timestamp` | `validFrom` and `work.completedAt` |
| `inputHash: {algorithm, value}` (hex) | `inputDigest` (SRI string) |
| `latencyMs` | `work.latencyMs` |
| `priceUsd` (float) | `price.amount` (decimal string) |
| `invocationNonce` | `nonce` |
| `parentReceipts` (ids) | `parents` (digest references) |
| `teeAttestation` | `environment.teeAttestation` |
| `zkInputProof` / `zkOutputProof` | `environment.zkProof.input` / `.output` |
| `providerHub` | `credentialSubject.providerHub` (still signed) and `issuer.name` |
| `hubInfo` (unsigned) | `hubInfo` (signed) |
| *(none)* | `work.status`, `awrVersion`, `payloadSerialization` |

Two AWR/1 renderings exist in the wild and both are tried, `hub-1` first:

- **`hub-1`** — what this plugin's issuer produced: `key:value|key:value`, top level only,
  nested containers rendered as a JSON-ish blob;
- **§12.1 dialect A / B** — `path=leaf` entries with dotted leaf paths, integers rendered
  as `2340` (A) or `2340.0` (B).

They disagree on every receipt, so a verifier that implements only §12.1 reports
`AWR-LEGACY-002` for receipts this hub signed correctly. `legacyDialect` in the result says
which rendering verified.
