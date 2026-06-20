# AIMarket Hub Plugins

<!-- aicom-readme-badges -->
<p align="center">
  [![CI](https://github.com/alexar76/aimarket-plugins/actions/workflows/ci.yml/badge.svg)](https://github.com/alexar76/aimarket-plugins/actions/workflows/ci.yml)
  <a href="#testing--coverage"><img src="docs/badges/coverage.svg" alt="Test coverage" /></a>
  [![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
</p>
<!-- /aicom-readme-badges -->


> **Ecosystem:** [AICOM overview & live demos](https://modeldev.modelmarket.dev)

**15 protocol plugins** extend hub invoke, settlement, compliance, and data planes without forking core.

**Verifiable math** lives in the separate **[oracles](https://github.com/alexar76/oracles)** monorepo (Platon, Chronos, Murmuration, Lumen, …) — listed on the hub like any capability. **`aimarket-reputation`** scores sellers; **Lumen** oracle supplies PageRank/EigenTrust-style trust artifacts agents can invoke and audit.

## Killer feature — TEE Escrow

**Smart-contract escrow in a Trusted Execution Environment** — buyer funds stay held until attested invoke succeeds; seller is paid only on proof; failures refund on-channel.

| | |
|---|---|
| **What** | `aimarket-tee` + provenance + safety → hold → invoke → release/refund |
| **Why** | Micropay scale with **both-side protection** — no human escrow desk |
| **Deep dive** | [docs/killer-feature-tee-escrow.md](docs/killer-feature-tee-escrow.md) · [Ecosystem killer features](../docs/killer-features.md) |

## MCP server — `aimarket-mcp-packager`

One of the 15 plugins is a full **[Model Context Protocol](https://modelcontextprotocol.io) server** — stdio transport, built on the official MCP Python SDK. [`aimarket-mcp-packager`](aimarket-mcp-packager/) turns any AIMarket capability into a self-hosted MCP product (Docker image, MCP manifest, and a ready `claude_desktop_config.json`) that runs in Claude Desktop, Cursor, Glama, or any stdio MCP client.

| | |
|---|---|
| **Run** | `python mcp_stdio_server.py` (stdio) · `docker build` from repo root |
| **Tools** | `package_capability` · `generate_dockerfile` · `generate_claude_desktop_config` |
| **Registry** | Listed on Glama (score badge above) — see [`aimarket-mcp-packager/`](aimarket-mcp-packager/) for the server, `glama.json`, and Dockerfile |

## MCP server — `aimarket-oracle-gateway` (standalone repo)

A **second MCP server** ships as its own satellite repo [`alexar76/aimarket-oracle-gateway`](https://github.com/alexar76/aimarket-oracle-gateway) for Glama indexing. [`aimarket-oracle-gateway`](aimarket-oracle-gateway/) exposes Platon randomness, Chronos VDF, and LUMEN reputation as agent-callable tools — pay-per-call over AIMarket, every result independently verifiable.

| | |
|---|---|
| **Run** | `AIMARKET_HUB_URL=https://modelmarket.dev python mcp_stdio_server.py` |
| **Tools** | `get_random` · `compute_vdf` · `get_reputation_scores` · `list_oracle_capabilities` · … |
| **Registry** | [![aimarket-oracle-gateway MCP server](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway/badges/score.svg)](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway) — [`glama.json`](glama.json) + [`Dockerfile`](Dockerfile) at repo root |

## Plugin index

| Plugin | Role |
|--------|------|
| [aimarket-safety](aimarket-safety/) | Pre-invoke policy, signed reject |
| [aimarket-tee](aimarket-tee/) | TEE attestation, escrow hooks |
| [aimarket-provenance](../aimarket-hub/plugins/aimarket-provenance/) | W3C VC invoke receipts |
| [aimarket-reputation](aimarket-reputation/) | Scores + stake bonds |
| [aimarket-channels](aimarket-channels/) | USDT channel lifecycle |
| [aimarket-auction](aimarket-auction/) | Price discovery |
| [aimarket-orchestrator](aimarket-orchestrator/) | Multi-capability plans |
| [aimarket-data-cap](aimarket-data-cap/) | Data-capability packaging |
| [aimarket-dataset](aimarket-dataset/) | Dataset listings |
| [aimarket-mcp-packager](aimarket-mcp-packager/) | **MCP server** (stdio) — package capabilities as Docker + MCP manifest + Claude Desktop config |
| [aimarket-nft](aimarket-nft/) | Capability NFTs |
| [aimarket-personas](aimarket-personas/) | Seller personas |
| [aimarket-promo](aimarket-promo/) | Promotions |
| [aimarket-streaming](aimarket-streaming/) | Streaming invoke |
| [aimarket-zk](aimarket-zk/) | ZK cohort proofs |

Hub loads plugins from [`aimarket-hub/plugins/`](../aimarket-hub/plugins/) at runtime. Monorepo copies under `plugins/` are the source-of-truth for docs and tests.
