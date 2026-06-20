# aimarket-oracle-gateway — MCP server

<!-- mcp-name: io.github.alexar76/aimarket-oracle-gateway -->

<!-- aicom-readme-badges -->
<p align="center">
  <a href="https://github.com/alexar76/aimarket-oracle-gateway/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexar76/aimarket-oracle-gateway/ci.yml?branch=main&label=CI" alt="CI" /></a>
  <a href="https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway"><img src="https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway/badges/score.svg" alt="aimarket-oracle-gateway MCP server" /></a>
  <a href="#testing--coverage"><img src="docs/badges/coverage.svg" alt="Test coverage" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT" /></a>
</p>
<!-- /aicom-readme-badges -->

**This repository ships a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server**
that exposes the AIMarket ecosystem's verifiable oracle capabilities — Platon VRF (signed
randomness), Chronos VDF (verifiable delay / proof-of-elapsed-time), and LUMEN reputation — as
agent-callable tools, pay-per-call over the AIMarket protocol.

Transport: **stdio** (`mcp_stdio_server.py`). Built with the official
**[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)** (`mcp` package, `FastMCP`).
Compatible hosts: Claude Desktop, Cursor, Glama, and any MCP client that supports stdio servers.

| Item | Location |
|------|----------|
| MCP entrypoint | [`mcp_stdio_server.py`](mcp_stdio_server.py) |
| MCP SDK dependency | [`requirements-mcp.txt`](requirements-mcp.txt) — `mcp>=1.6,<4` |
| Glama / Docker run | [`Dockerfile`](Dockerfile), [`glama.json`](glama.json) |

---

A **passthrough MCP server** that puts the ecosystem's oracle capabilities in front of any AI
agent. It is the discovery + consumption storefront for the
[oracle-as-a-service specs](../../docs/specs/README.md): an agent adds this server, sees the tools,
and calls them — **pay-per-call over the AIMarket protocol, no signup**. Every result is
**independently verifiable** (verify the signature / VDF proof rather than trusting the service).

## Tools
| MCP tool | capabilityId | price | what it returns |
|---|---|---|---|
| `get_random` | `platon.random@v1` | ~$0.004 | Ed25519-signed unbiasable randomness + proof |
| `get_randomness_beacon` | `platon.beacon@v1` | ~$0.004 | the round's public beacon |
| `ask_oracle` | `platon.ask@v1` | ~$0.003 | entropy-derived read-only answer |
| `compute_vdf` | `chronos.eval@v1` | ~$0.01 | VDF `y=g^(2^T)` + Wesolowski proof (proof-of-elapsed-work) |
| `verify_vdf` | `chronos.verify@v1` | ~$0.001 | `{valid}` for a VDF proof |
| `get_reputation_scores` | `lumen.reputation@v1` | ~$0.005 | PageRank/EigenTrust trust scores over a graph you supply |
| `list_oracle_capabilities` | — | free | the tool/capability/price table |

## Configure (env)
| var | meaning |
|---|---|
| `AIMARKET_HUB_URL` | AIMarket Hub base URL — **recommended**: metered + paid, the routing fee funds the ecosystem |
| `AIMARKET_ORACLE_URL` | direct oracle-family URL (used if no hub) — demo/free path |
| `AIMARKET_PAYMENT_CHANNEL` | optional pre-opened payment channel id (sent as `X-Payment-Channel`) |
| `AIMARKET_API_TOKEN` | optional bearer token (dev/prod auth) |
| `AIMARKET_MAX_PER_CALL_USD` | hard cap per call (default `0.10`) — a call advertised above this is **refused** |
| `AIMARKET_MAX_SPEND_USD` | hard cumulative budget this process (default `5.0`) — refused once exceeded |
| `AIMARKET_PRICE_TOLERANCE` | reject if the hub charges > advertised × (1 + this) (default `0.10`) — overcharge guard |

If neither URL is set the server fails closed with a clear message (never silently fakes a result).

**Payment safety (spec 03):** the caps above are enforced client-side and a prompt-injected /
runaway agent **cannot** override them — the gateway refuses to spend past them (fail-closed). It
also rejects a hub that charges more than the advertised price. See
[docs/specs/03-mcp-payment-and-security.md](../../docs/specs/03-mcp-payment-and-security.md).

## Run
```bash
# stdio (local / Claude Desktop)
pip install -r requirements-mcp.txt && pip install --no-deps -e .
AIMARKET_HUB_URL=https://modelmarket.dev python mcp_stdio_server.py

# Docker
docker build -t aimarket-oracle-gateway .
docker run -i -e AIMARKET_HUB_URL=https://modelmarket.dev aimarket-oracle-gateway
```

Claude Desktop (`mcpServers` entry):
```json
{ "mcpServers": { "aimarket-oracle-gateway": {
  "command": "python", "args": ["mcp_stdio_server.py"],
  "env": { "AIMARKET_HUB_URL": "https://modelmarket.dev" } } } }
```

## Publish on Glama (maintainer)

[![aimarket-oracle-gateway MCP server](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway/badges/score.svg)](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway)

This repo ships `glama.json` + `Dockerfile` at the root. Listing: **[glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway)** — claim with **Login with GitHub** (`maintainers`: `alexar76`). Glama builds the Docker image and indexes the tools so agents discover `get_random` / `compute_vdf` / … directly.

## Verifiability (the point)
- **Randomness** carries an Ed25519 `signature` over `(random_hex ‖ proof)`; verify against the
  signer key in the Hub's signed manifest. The result includes a `verifiable` summary (`signed`,
  `has_proof`).
- **VDF** output `y=g^(2^T) mod N` is checkable in one exponentiation via `verify_vdf` (and on-chain
  via `ChronosVDF.sol`).

## Notes
- This is the storefront; the live oracles + escrow settlement already exist (see
  [docs/onchain-journal.md](../../docs/onchain-journal.md)).
- Shipped: `platon.verify` / `lumen.verify` / `lumen.score`, `chronos modulus_hex`, the hub's
  signed `.well-known` + `/prices` endpoint, and the gateway spending-cap/overcharge security core.
- Roadmap (per the specs): the full channel lifecycle (auto open/sign/settle/refund, nonce-sync,
  receipt-signature + TLS pinning), the remote pre-signed-voucher path, and OS-keychain key storage.
- Tests: `PYTHONPATH=. pytest tests/` (routing/parse logic, no network).
