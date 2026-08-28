# aimarket-data-cap — User Guide

## What it does

Private RAG corpus exposed as paid search capability. Category: **monetization**.

## Installation

```bash
pip install aimarket-data-cap
aimarket serve
curl http://localhost:9080/ai-market/v2/plugins | jq '.plugins[] | select(.name=="aimarket-data-cap")'
```

## Hub integration

Plugins register via setuptools entry point `aimarket.plugins`. After install, restart the hub — routes mount under `/ai-market/v2/p/{plugin_name}/`.

Invoke hooks: none

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/ai-market/v2/p/aimarket-data-cap/manifest` | Build the listing for a corpus you serve |

There is no `/index` and no `/search`, and there never was — earlier versions of this table
listed both. **The hub stores no corpus and runs no index.** Your data stays behind your own
endpoint; this plugin builds the listing, `/ai-market/v2/supply/register` publishes it, and
buyers' calls arrive at your `invoke_url`. Your share of each sale
(`AIMARKET_PUBLISHER_SHARE_BPS`, 70% by default) is credited to your account on the hub as
the calls complete.

## Configuration

See plugin README for environment variables. Common hub vars:

| Variable | Description |
|----------|-------------|
| `AIMARKET_HUB_URL` | Public hub URL in receipts/manifest |
| `DATABASE_URL` | Optional PostgreSQL (SQLite default) |

## Verify loaded

```bash
curl http://localhost:9080/.well-known/ai-market.json | jq '.plugin_extensions.data-cap'
```

## More

- [SDK integration](sdk-integration.md)
- [User cases](user-cases.md)
- [README](../README.md)
