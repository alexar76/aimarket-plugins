# aimarket-provenance — User Guide

## What it does

Cryptographic provenance receipts for every AI output (Ed25519 + W3C VC). Category: **compliance**.

## Installation

```bash
pip install -e aimarket-hub/plugins/aimarket-provenance
aimarket serve
curl http://localhost:9080/ai-market/v2/plugins | jq '.plugins[] | select(.name=="provenance")'
```

## Hub integration

Plugins register via setuptools entry point `aimarket.plugins`. After install, restart the hub — routes mount under `/ai-market/v2/p/{plugin_name}/`.

Invoke hooks: `on_invoke_post_check`

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ai-market/v2/p/provenance/attest` | Create provenance receipt (Bearer auth optional) |
| `GET` | `/ai-market/v2/p/provenance/receipt/{id}` | Fetch stored receipt |
| `GET` | `/ai-market/v2/p/provenance/verify/{id}` | Verify signature + chain |

## Configuration

See plugin README for environment variables. Common hub vars:

| Variable | Description |
|----------|-------------|
| `AIMARKET_HUB_URL` | Public hub URL in receipts/manifest |
| `DATABASE_URL` | Optional PostgreSQL (SQLite default) |

## Verify loaded

```bash
curl http://localhost:9080/.well-known/ai-market.json | jq '.plugin_extensions.provenance'
```

## More

- [SDK integration](sdk-integration.md)
- [User cases](user-cases.md)
- [README](../README.md)
