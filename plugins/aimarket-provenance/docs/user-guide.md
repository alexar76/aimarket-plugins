# aimarket-provenance — User Guide

## What it does

AWR/2 work receipts for every AI output — W3C Verifiable Credentials with an
`eddsa-jcs-2022` Data Integrity proof over RFC 8785 canonical bytes, issued by a `did:key`.
Category: **compliance**. Format: [AWR/2](https://github.com/alexar76/aicom/blob/main/awr/SPEC.md).

## Installation

```bash
pip install -e aimarket-hub/plugins/aimarket-provenance   # pulls in awr>=2.0,<3
aimarket serve
curl http://localhost:9080/ai-market/v2/plugins | jq '.plugins[] | select(.name=="provenance")'
```

`awr` is a hard dependency and there is no fallback: if it cannot be imported the plugin
raises `ProvenanceDependencyError` at import time. That is deliberate — canonicalization and
proofs live in exactly one place, because a second copy of them is what split AWR/1 into two
incompatible dialects. From a monorepo checkout: `pip install -e awr/reference/python`.

## Hub integration

Plugins register via the setuptools entry point `aimarket.plugins`. After install, restart
the hub — routes mount under `/ai-market/v2/p/{plugin_name}/`.

Invoke hooks: `on_invoke_post_check` (attaches a receipt; never blocks).

## The signing key is now an identity

On first run the plugin creates an Ed25519 key at `AIMARKET_PROVENANCE_KEY_PATH` and logs
the `did:key` derived from it:

```
Provenance signing key loaded (did:key: did:key:z6Mkgs7XWJuz…, fingerprint: …, path: data/provenance_signing_key)
```

That DID is the receipt's `issuer.id`, and a verifier derives the public key **from it**
(SPEC.md §5.1) — there is no separate embedded key that could disagree with it. Back the key
file up. AWR has no revocation and no rotation beyond publishing a new `did:key` (§5.4,
§13.6), so a lost key means a new identity, not a recoverable one. Receipts already issued
stay verifiable regardless: they carry everything a verifier needs.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ai-market/v2/p/provenance/attest` | Create a receipt (Bearer token required) |
| `GET` | `/ai-market/v2/p/provenance/receipt/{id}` | Fetch the stored AWR/2 document |
| `GET` | `/ai-market/v2/p/provenance/verify/{id}` | Verify: signature, chain, profile |

`GET /verify/{id}?profile=L1` requests a profile (§10). Without it you get L0 — a single
valid receipt — which is all a receipt on its own can be.

## Verifying a receipt

```bash
RID=urn:uuid:d08663d5-f5e7-4868-805d-b917a429781f

# this hub
curl -s "http://localhost:9080/ai-market/v2/p/provenance/verify/$RID" | jq '.valid, .awr.profile'

# independently, offline, with the reference CLI
curl -s "http://localhost:9080/ai-market/v2/p/provenance/receipt/$RID" > receipt.json
python -m awr verify receipt.json | jq '.valid, .verifiedProof'

# or paste receipt.json into https://verify.modelmarket.dev
```

Reading the result:

- `valid` is true if and only if no reason has severity `error` (§11.1);
- `warnings` are not failures. `AWR-ENV-001` means an attestation is present and was **not**
  verified; `AWR-TIME-002` means the receipt is past its `validUntil`, which is policy, not
  validity (§11.3);
- `profile: null` with `valid: true` is normal for anything that is not a receipt;
- `checks[].issuer_binding.bound: false` means no pinned key applied — the signature is
  sound, but whether that `did:key` is the party you meant is a trust question AWR does not
  answer (§13.7).

## Configuration

See the plugin README for the full table. The ones that matter most:

| Variable | Description |
|----------|-------------|
| `AIMARKET_PROVENANCE_KEY_PATH` | Ed25519 signing key → your `did:key` identity |
| `AIMARKET_PROVENANCE_API_TOKEN` | Required for `/attest`; unset means `/attest` returns 503 |
| `AIMARKET_HUB_URL` | Public hub URL used in `verify_url` / `receipt_url` |
| `AIMARKET_VERIFY_DOMAIN` | Offline verifier UI advertised as `verifier_url` |
| `DATABASE_URL` | Optional PostgreSQL (SQLite default) |

## Upgrading from 1.x

No database migration to run: the schema is unchanged and old rows are never rewritten.
New receipts are AWR/2, old receipts stay AWR/1 and stay verifiable. Two call-site changes:

- `parent_receipts` on `/attest` must resolve to a receipt this hub stores, or be a full
  digest reference. An identifier alone is no longer an edge (§3.2, §8.1, §13.1).
- `price_usd: 0.15` is still accepted on the request and becomes `price.amount: "0.15"` in
  the document. If you read receipts programmatically, read the string.

## Verify loaded

```bash
curl http://localhost:9080/.well-known/ai-market.json | jq '.plugin_extensions.provenance'
```

## More

- [Receipt format](receipt-format.md)
- [SDK integration](sdk-integration.md)
- [User cases](user-cases.md)
- [README](../README.md)
- [AWR/2 specification](https://github.com/alexar76/aicom/blob/main/awr/SPEC.md)
