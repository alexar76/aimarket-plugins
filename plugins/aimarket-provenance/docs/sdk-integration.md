# aimarket-provenance — SDK Integration

## Quick integration

```python
import requests

HUB = "http://localhost:9080"  # or https://modelmarket.dev

# 1. Confirm the plugin is loaded
plugins = requests.get(f"{HUB}/ai-market/v2/plugins").json()
assert any(p["name"] == "provenance" for p in plugins["plugins"])

# 2. Auto-attached on every invoke response
r = requests.post(f"{HUB}/ai-market/v2/invoke", json={...}).json()
print(r.get("provenance_receipt"))
# {"receipt_id": "urn:uuid:…", "verify_url": "…/verify/urn:uuid:…",
#  "receipt_url": "…/receipt/urn:uuid:…", "verifier_url": "https://verify.modelmarket.dev",
#  "awr_version": "2.0.0", "issuer": "did:key:z6Mk…"}

# 3. Manual attest
requests.post(
    f"{HUB}/ai-market/v2/p/provenance/attest",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={
        "model_id": "translate@v1",
        "input": {"text": "Hello"},
        "output": {"text": "Bonjour"},
        "status": "succeeded",
        "latency_ms": 320,
        "price_usd": 0.002,
    },
)
```

`receipt_id` and `verify_url` are the two keys this contract has always carried;
`receipt_url`, `verifier_url`, `awr_version` and `issuer` are additive. `verify_url` and
`receipt_url` are absolute when `AIMARKET_HUB_URL` is set and root-relative otherwise.

## Invoke hook behavior

When this plugin registers invoke hooks, the hub calls them automatically on every
`/ai-market/v2/invoke`:

1. **Pre-check** — can block input (safety, ZK input proof, promo validation)
2. **Post-check** — can block output or attach metadata (provenance receipt, TEE attestation)

Provenance uses the post-check only and **never blocks**: a failure to issue a receipt is
logged, not raised. Blocked invocations elsewhere return HTTP 403 with a signed rejection
receipt and a channel refund when applicable.

## Manifest extension

After install, the hub merges plugin fields into `/.well-known/ai-market.json` under
`plugin_extensions`:

```json
{
  "provenance": {
    "version": "2.0.0",
    "receipt_format": "AWR/2 (W3C Verifiable Credential)",
    "awr_version": "2.0.0",
    "specification": "https://verify.modelmarket.dev/ns/awr/v2",
    "proof_type": "DataIntegrityProof",
    "cryptosuite": "eddsa-jcs-2022",
    "canonicalization": "RFC 8785 (JCS)",
    "issuer_identity": "did:key",
    "signing_algorithm": "Ed25519",
    "features": {
      "auto_receipt": true,
      "tee_attestation": true,
      "attestations_verified": false,
      "legacy_awr1_verification": true,
      "legacy_awr1_issuance": false,
      "offline_verifiable": true
    }
  }
}
```

`attestations_verified: false` is not a gap to be filled in later by this plugin. Verifying
a TEE attestation needs the platform's certificate chain (AWS Nitro, Intel TDX, AMD SEV,
Azure CC), which is a network- and vendor-dependent operation that an offline verifier must
not perform (SPEC.md §7.3). Advertising it as verified is exactly the misrepresentation that
section was written about.

## Python package import

```python
from aimarket_hub.signing import Signer
from aimarket_provenance.receipt import ProvenanceReceipt
from aimarket_provenance.verifier import verify_receipt

signer = Signer(key_path="data/provenance_signing_key")

receipt = ProvenanceReceipt.create(
    model_id="claude-sonnet-5@anthropic",
    provider_hub="https://hub.example.com",
    input_payload={"prompt": "Hello"},
    output_payload={"text": "Hi"},
    signer=signer,
    latency_ms=320,
    price_usd=0.002,
)

receipt.receipt_id      # "urn:uuid:…"
receipt.issuer_id       # "did:key:z6Mk…"  — the key is derived from this
receipt.input_hash      # "sha256-…"       — an SRI string, not hex
receipt.price_amount    # "0.002"          — the signed decimal string
receipt.to_dict()       # the AWR/2 document
receipt.verify()        # True

result = verify_receipt(receipt)
result.valid            # True
result.awr              # the verbatim SPEC.md §11.1 result
result.awr["profile"]   # "L0"
```

`ProvenanceReceipt` wraps the AWR document rather than being a struct it is rebuilt from,
and its attributes are **read-only views** over `receipt.document`. That is not cosmetic:
§4.2 forbids re-canonicalizing a document through a lossy intermediate representation,
because the bytes then differ from the ones the issuer signed, and a typed struct that drops
unknown fields is precisely such a representation. To simulate tampering in a test, mutate
`receipt.document` — which is what an intermediary does, and what now breaks the signature.

### Chains

```python
parent = ProvenanceReceipt.create(..., signer=signer)
child = ProvenanceReceipt.create(..., signer=signer, parent_receipts=[parent])

child.parents          # [{"id": "urn:uuid:…", "digestSRI": "sha256-…"}]
verify_receipt(child, parents=[parent]).awr["chain"]   # {"resolved": 1, "unresolved": 0}
verify_receipt(child).awr["chain"]                     # {"resolved": 0, "unresolved": 1}
```

An unresolved edge is not an error: a verifier must not fetch a parent (§13.5), so
"chain not checked" is a distinct answer from "chain intact" (§8.2). Passing a bare
identifier string to `parent_receipts` raises — an id-only edge is re-pointable (§13.1).

### Reading a legacy receipt

```python
receipt = ProvenanceReceipt.from_json(stored_json)   # AWR/1 or AWR/2, detected
receipt.is_legacy                                    # True for Ed25519Signature2018
receipt.awr_version                                  # None — AWR/1 carries none
receipt.verify()                                     # True — still verifiable
result = verify_receipt(receipt)
result.awr["unsignedFields"]                         # ["id", "type", "issuer", …]
```

Prefer `from_json` over `from_dict` for anything read from storage or off the wire: the §4.3
number check is lexical over the received bytes and §4.1 requires duplicate property names
to be rejected, and both are unrecoverable after a permissive parse.

## Related plugins

See the [AIMarket Hub README](https://github.com/alexar76/aimarket-hub/blob/main/README.md#14-plugins)
for the full plugin catalog.
