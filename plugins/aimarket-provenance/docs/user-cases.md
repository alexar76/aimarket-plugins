# aimarket-provenance — User Cases

### Compliance officer

Archive the AWR/2 document per regulated AI decision. It is self-contained: a `did:key`
issuer, digests of input and output, model, timestamps, and a whole-document Ed25519
signature. Years later it verifies with no registry, no revocation list and no call to the
hub that issued it — and age is a warning, never a failure (SPEC.md §11.3), because an audit
is the main reason old receipts are read.

### Consumer app

Show the receipt next to every AI answer: fetch `receipt_url`, offer `verifier_url` for an
independent check. Do not render validity as a bare green check — a valid receipt means *this
issuer signed these claims*, not that the output is correct (§13.7), so show the issuer
identity beside it.

### Multi-step pipeline

Chain hops with `parents`. Each edge commits to the parent receipt's exact bytes, so a chain
cannot be re-pointed at a substituted document while staying valid (§8.1). Verify the whole
DAG by supplying the parents you hold:

```bash
python -m awr verify final.json --parents hop1.json hop2.json retrieval.json
```

A resolved chain proves each hop's issuer committed to its parent's bytes. It does **not**
prove the parent's output was the child's input — only that the child's issuer said so
(§8.3).

### Dispute over a failed call

Issue the receipt anyway, with `status: failed|refused|timeout|partial`. A receipt for work
that did not succeed is a first-class document (§3.3), and an unverifiable failure is what a
dispute usually turns on. Who is answerable for which hop is a separate signed document —
AWR's `BlameAttestation` (§3.5), which this plugin does not issue.

## Cross-plugin workflows

| Combine with | Workflow |
|--------------|----------|
| `aimarket-channels` | Pre-fund a session; `price.amount` records what each receipt debited |
| `aimarket-safety` | Block unsafe calls before a paid invoke; the rejection is its own receipt |
| `aimarket-tee` | Attestation under `environment.teeAttestation` — carried inside the signature, never claimed as verified |
| `aimarket-reputation` | `reputationScore` as a decimal string on attest |
