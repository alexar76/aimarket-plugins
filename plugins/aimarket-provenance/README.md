# aimarket-provenance

**AWR/2 work receipts for every AI output — W3C Verifiable Credentials, `eddsa-jcs-2022` over RFC 8785, issued by a `did:key`.**

Infrastructure-layer plugin for [AIMarket Hub](https://pypi.org/project/aimarket-hub/). Auto-attaches a tamper-evident receipt to every successful `/invoke`, and exposes public verify endpoints for auditors and end users.

Receipts conform to [**AWR/2**](https://github.com/alexar76/aicom/blob/main/awr/SPEC.md) (Agent Work Receipt 2.0.0). Verification needs no network, no registry, no blockchain and no issuer-specific software: any conformant W3C VC library can check one.

> **Try it:** open the receipt's `receipt_url`, copy the JSON, and paste it into **[verify.modelmarket.dev](https://verify.modelmarket.dev)** — client-side Ed25519, no backend, nothing sent anywhere.

## Documentation

Every link here is absolute on purpose. A relative link in this file resolves against
`pypi.org` on the project page and 404s, and these documents are not inside the distribution
either, so a reader has nowhere else to follow them to.

| Document | Description |
|----------|-------------|
| [User guide](https://github.com/alexar76/aimarket-plugins/blob/main/plugins/aimarket-provenance/docs/user-guide.md) | Install, configure signing key, verify receipts |
| [Receipt format](https://github.com/alexar76/aimarket-plugins/blob/main/plugins/aimarket-provenance/docs/receipt-format.md) | The AWR/2 document this plugin emits, field by field |
| [User cases](https://github.com/alexar76/aimarket-plugins/blob/main/plugins/aimarket-provenance/docs/user-cases.md) | Compliance, consumer apps, multi-step chains |
| [SDK integration](https://github.com/alexar76/aimarket-plugins/blob/main/plugins/aimarket-provenance/docs/sdk-integration.md) | HTTP API, Python, invoke hook behavior |
| [AWR/2 specification](https://github.com/alexar76/aicom/blob/main/awr/SPEC.md) | The normative format definition |

---

## Why this exists

Regulated and high-trust AI workflows need proof of **what model ran, on what input digest, at what time, signed by whom** — without storing full prompts anywhere. Provenance is the **infrastructure abstract layer** between raw invoke results and external audit systems.

What a valid receipt means, exactly: *this issuer signed these claims, and the bytes are intact.* It does not mean the model ran, that the digests correspond to real payloads, that the price was paid, or that the output is correct (SPEC.md §13.7). Everything AWR provides is attribution.

## Features

- **Auto-receipt on invoke** — `provenance_receipt` on every invoke response
- **AWR/2 / W3C Verifiable Credential**, Ed25519, whole-document signature
- **Content-addressed provenance chains** — `parents` commit to the parent receipt's exact bytes
- **TEE + ZK metadata** — carried inside the signature, and never claimed as verified
- **Offline public verify** — anyone, with no call back to this hub
- **Protected attest** — Bearer token required for manual receipt creation
- **AWR/1 legacy verification** — receipts issued before this release still verify

## Installation

```bash
pip install aimarket-provenance
aimarket serve
```

That pulls `aimarket-hub`, `awr>=2.0,<3` and `fastapi` from PyPI. The plugin registers itself
through the `aimarket.plugins` entry point, so the hub discovers it with no configuration.

The `awr` package is a **hard** dependency. It is where RFC 8785 canonicalization, the `eddsa-jcs-2022` proof and `did:key` derivation live, and the plugin implements none of them itself. If it is missing, the plugin raises `ProvenanceDependencyError` at import and the hub does not start with provenance quietly disabled — a receipt signed by a second canonicalizer verifies for its author and for nobody else, which is what split AWR/1 into two incompatible dialects (SPEC.md §4.3, Appendix D). It is [`awr` on PyPI](https://pypi.org/project/awr/) and needs no special handling.

From a checkout of this monorepo instead, where both live in the tree:

```bash
pip install -e awr/reference/python
cd aimarket-hub && pip install -e plugins/aimarket-provenance
```

Verify:

```bash
curl http://localhost:9083/ai-market/v2/plugins | jq '.plugins[] | select(.name=="provenance")'
curl http://localhost:9083/.well-known/ai-market.json | jq '.plugin_extensions.provenance'
```

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/ai-market/v2/p/provenance/attest` | Bearer (required) | Create a receipt manually |
| `GET` | `/ai-market/v2/p/provenance/receipt/{id}` | Public | Fetch the stored AWR/2 document |
| `GET` | `/ai-market/v2/p/provenance/verify/{id}` | Public | Full verification, SPEC.md §11.1 result |

`GET /verify/{id}` accepts `?profile=L0|L1|L2` to request a profile (§10). Profile reason codes are only reported for a profile you asked for (§10.4).

## Auto-receipt on invoke

```bash
curl -X POST http://localhost:9083/ai-market/v2/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "prod-demo",
    "capability_id": "translate@v1",
    "source_hub": "local",
    "input": {"text": "Hello"}
  }' | jq '.provenance_receipt'
```

```json
{
  "receipt_id": "urn:uuid:d08663d5-f5e7-4868-805d-b917a429781f",
  "verify_url": "https://modelmarket.dev/ai-market/v2/p/provenance/verify/urn:uuid:d08663d5-f5e7-4868-805d-b917a429781f",
  "receipt_url": "https://modelmarket.dev/ai-market/v2/p/provenance/receipt/urn:uuid:d08663d5-f5e7-4868-805d-b917a429781f",
  "verifier_url": "https://verify.modelmarket.dev",
  "awr_version": "2.0.0",
  "issuer": "did:key:z6Mkgs7XWJuzXfe7CiuAqEioZxm6GYcb7SQr9yEYWwV7PJod"
}
```

**The verify URL changed in 2.0.0.** It used to be `{AIMARKET_VERIFY_DOMAIN}/r/{short_id}`, which was a dead link: the static verifier at `verify.modelmarket.dev` never reads the request path (nginx serves `index.html` for `/r/…` and the page's only lookup is over pasted text), and it can only fetch documents it hosts itself under `data/receipts/`, never a receipt stored on a hub. Opening such a link showed an empty form. The plugin now emits URLs that work:

- `verify_url` and `receipt_url` — this hub's own public routes, absolute when `AIMARKET_HUB_URL` is set and root-relative otherwise;
- `verifier_url` — the offline verifier UI, where the document from `receipt_url` can be pasted. That is the *offline* check: it needs nothing from this hub and tells this hub nothing (SPEC.md §13.5, §14).

## Manual attest

```bash
curl -X POST http://localhost:9083/ai-market/v2/p/provenance/attest \
  -H "Authorization: Bearer $AIMARKET_PROVENANCE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model_id": "legal.review@v1@prod-legal",
    "provider_hub": "https://provider.example.com",
    "input": {"documents": {"hash": "sha256:..."}},
    "output": {"risk": "low", "issues": 0},
    "status": "succeeded",
    "parent_receipts": ["urn:uuid:2c1e…"],
    "latency_ms": 4200,
    "price_usd": 0.15
  }'
```

Request fields: `model_id`, `input`, `output` are required. `status` is one of `succeeded`, `failed`, `refused`, `timeout`, `partial` (default `succeeded`) — a receipt for work that did not succeed is a first-class document, because an unverifiable failure is what a dispute usually turns on. `provider_hub`, `latency_ms`, `price_usd`, `currency`, `invocation_nonce`, `reputation_score`, `tee_attestation`, `zk_input_proof`, `zk_output_proof`, `settlement` and `parent_receipts` are optional.

**`parent_receipts` changed in 2.0.0.** An AWR/2 chain edge commits to the parent's exact bytes (SPEC.md §3.2, §8.1), so an identifier alone is not an edge — in AWR/1 it was, and because `id` was outside the signature an intermediary could rename a valid receipt and re-point a chain at it (§13.1). You may pass:

- a receipt id **this hub stores** — the hub loads it and computes the digest itself;
- a full digest reference `{"id": "urn:uuid:…", "digestSRI": "sha256-…"}`, when the parent lives elsewhere.

An id that resolves to nothing is a `400`. The hub will not sign a commitment to bytes it has never seen, and a verifier must not fetch them (§13.5).

## What a receipt looks like

```json
{
  "@context": [
    "https://www.w3.org/ns/credentials/v2",
    "https://verify.modelmarket.dev/ns/awr/v2"
  ],
  "id": "urn:uuid:d08663d5-f5e7-4868-805d-b917a429781f",
  "type": ["VerifiableCredential", "WorkReceipt", "AIProvenanceReceipt"],
  "issuer": { "id": "did:key:z6Mkgs7XWJuzXfe7CiuAqEioZxm6GYcb7SQr9yEYWwV7PJod", "name": "AIMarket Hub" },
  "validFrom": "2026-08-01T16:22:50Z",
  "awrVersion": "2.0.0",
  "credentialSubject": {
    "work": {
      "modelId": "translate@v1@prod-demo",
      "startedAt": "2026-08-01T16:22:48Z",
      "completedAt": "2026-08-01T16:22:50Z",
      "latencyMs": 2340,
      "status": "succeeded"
    },
    "inputDigest": "sha256-G4ESnt/c8X4GQdS1pUTbbWwQDF7wNB+EXxgEoTlBA5s=",
    "outputDigest": "sha256-I6HZM9DzHKwCl/fNyIxyAkNCx8R2+eHWHqQ9ZbeZqbQ=",
    "providerHub": "https://hub.example.com",
    "payloadSerialization": "json-sorted-compact",
    "price": { "currency": "USD", "amount": "0.15" },
    "nonce": "20cd34fe-0acf-4173-af48-c78e4d24de35"
  },
  "hubInfo": { "hubName": "AIMarket Hub", "hubVersion": "3.0.0", "protocolVersion": "v2" },
  "proof": {
    "type": "DataIntegrityProof",
    "cryptosuite": "eddsa-jcs-2022",
    "created": "2026-08-01T16:22:50Z",
    "verificationMethod": "did:key:z6Mkgs7XWJuzXfe7CiuAqEioZxm6GYcb7SQr9yEYWwV7PJod#z6Mkgs7XWJuzXfe7CiuAqEioZxm6GYcb7SQr9yEYWwV7PJod",
    "proofPurpose": "assertionMethod",
    "proofValue": "zHrSm2kM5fQq9bb7Q23V1wqasPdiPyujUFp8qcrzHM4kAnbvyZNNgYMJx6ZNgBepEmpydnoxRNTVhxkqNUq793gC"
  }
}
```

Field-by-field, including the hub-specific members (`providerHub`, `payloadSerialization`, `reputationScore`, `hubInfo`) and how each AWR/1 field maps: [docs/receipt-format.md](https://github.com/alexar76/aimarket-plugins/blob/main/plugins/aimarket-provenance/docs/receipt-format.md).

## Migrating from 1.x (AWR/1 → AWR/2)

| Then (AWR/1, ≤1.1.0) | Now (AWR/2, 2.0.0) | Why |
|---|---|---|
| signature over `credentialSubject` only | whole document | `id`, `type` and `issuer` were unsigned, so a receipt could be renamed and a chain re-pointed (§13.1) |
| a "JCS" canonicalizer with NFC, code-point key order and 10-decimal floats | RFC 8785, exactly, in `awr` | two implementations of one format disagreed on the bytes for any integer |
| `priceUsd: 0.15`, `reputationScore: 0.9` (JSON numbers) | `price.amount: "0.15"`, `reputationScore: "0.87"` (decimal strings) | removes the int/float divergence instead of arbitrating it (§4.3) |
| `Ed25519Signature2018`, base64 `proofValue` | `DataIntegrityProof` + `eddsa-jcs-2022`, multibase `z…` | off-the-shelf VC libraries verify the current suite |
| `issuer.id` = `did:key:` + 32 base64 chars | a real `did:key`, and the key is derived from it | the legacy value was not a DID and named no key |
| `parentReceipts: ["urn:uuid:…"]` | `parents: [{"id":…, "digestSRI":…}]` | content-addressed edges (§8.1) |
| receipts older than 90 days hard-failed | age is a warning (`AWR-TIME-002`) | an audit is the main reason old receipts are read (§11.3) |
| TEE attestation "verified" with the receipt issuer's own key | `AWR-ENV-001`, present and not verified | the old check proved only that the claimant wrote it down (§7.3) |

**AWR/1 issuance is gone.** SPEC.md §12 requires an implementation never to issue AWR/1, so there is no flag, parameter or environment variable that produces one — the capability is absent, not guarded.

**AWR/1 verification stays.** Every receipt already in your database still verifies, reports the `AWR-LEGACY-001` warning, names its unsigned fields, and never claims an AWR/2 profile. Nothing is re-signed: §12.2 forbids it, since an issuer cannot honestly re-attest a `created` timestamp.

One thing to know if you compare notes with another AWR implementation: the pipe-delimited AWR/1 rendering written out in SPEC.md §12.1 (`path=leaf`, dotted leaf paths) is **not** the rendering this plugin's AWR/1 issuer produced (`key:value`, top level only). The two disagree on every receipt, so the reference verifier reports `AWR-LEGACY-002` for a receipt this hub signed correctly. `aimarket_provenance/legacy.py` therefore keeps its own rendering (dialect `hub-1`) and tries it *before* the two §12.1 dialects, so both foreign and local AWR/1 documents verify. This is reported upstream as a spec finding.

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `AIMARKET_PROVENANCE_KEY_PATH` | `data/provenance_signing_key` | Ed25519 signing key (created on first run) |
| `AIMARKET_PROVENANCE_API_TOKEN` | *(empty)* | Bearer token for `/attest`; empty = `/attest` returns 503 |
| `AIMARKET_HUB_URL` | *(empty)* | Public hub base URL used in `verify_url` / `receipt_url` |
| `AIMARKET_VERIFY_DOMAIN` | `https://verify.modelmarket.dev` | Offline verifier UI in `verifier_url` |
| `AIMARKET_RECEIPT_CORS_ORIGINS` | verifier + USE origins | Exact browser origins allowed to read public receipt JSON; GET route only |
| `DATABASE_URL` | SQLite | Optional PostgreSQL for receipt storage |

**Back up `provenance_signing_key`.** Under AWR/2 the issuer identifier *is* the key: `issuer.id` is the `did:key` derived from it, logged at startup. Lose the key and you cannot issue under that identity again; AWR has no revocation and no rotation mechanism beyond publishing a new `did:key` (§5.4, §13.6). Historical receipts stay verifiable either way — they carry everything a verifier needs.

## Storage

Receipts persist in `provenance.db` (SQLite) or PostgreSQL when `DATABASE_URL` is set. Migration `005_provenance_receipts` in the hub schema, **unchanged**: same columns, same indexes, no new migration, so a 1.x database is read by 2.0.0 and vice versa.

Four columns keep their name and meaning while the encoding of *new* rows changes. Old rows are never rewritten, and which generation a row is, is readable from the row itself (`raw_json` → `proof.type`).

| Column | AWR/1 rows | AWR/2 rows |
|---|---|---|
| `input_hash`, `output_hash` | 64-char hex | SRI string, `sha256-<base64>` |
| `issuer_pubkey_b64` | key asserted by the document | key **derived** from the `did:key` |
| `proof_value` | base64 signature | multibase base58btc, `z…` |
| `parent_receipts` | JSON array of id strings | JSON array of digest references |

`raw_json` holds the document verbatim and is the only column verification reads: §4.2 forbids re-canonicalizing through a lossy intermediate representation, and every other column is exactly such a projection. `price_usd REAL` stays a float column for querying — the signed value is the decimal string `price.amount`.

## Combine with

| Plugin | Pattern |
|--------|---------|
| `aimarket-tee` | TEE attestation under `environment.teeAttestation` (carried, not verified) |
| `aimarket-zk` | ZK proof references under `environment.zkProof` without revealing payloads |
| `aimarket-reputation` | `reputationScore` decimal string on attest |
| `aimarket-channels` | `price.amount` records what was debited from the channel |

## License

Apache-2.0 · Part of AIMarket Hub
