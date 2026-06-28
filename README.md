<!-- aicom-mirror-notice -->
> **📖 Read-only mirror.** `aimarket-plugins` is published from the canonical AI-Factory monorepo.
> **Pull requests are not accepted** — any commit pushed here is overwritten by
> `scripts/mirror_satellites.sh` on the next sync.
> 🐞 Found a bug or have a request? Please **[open an issue](https://github.com/alexar76/aimarket-plugins/issues)**.

# AIMarket Hub Plugins

<!-- aicom-readme-badges -->
<p align="center">
  <a href="https://github.com/alexar76/aimarket-plugins/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/alexar76/aimarket-plugins/ci.yml?branch=main&label=CI" alt="CI" /></a>
  <a href="https://glama.ai/mcp/servers/alexar76/aimarket-plugins"><img src="https://glama.ai/mcp/servers/alexar76/aimarket-plugins/badges/score.svg" alt="aimarket-plugins MCP server" /></a>
  <a href="#testing--coverage"><img src="docs/badges/coverage.svg" alt="Test coverage" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache--2.0-blue.svg" alt="License: Apache-2.0" /></a>
</p>
<!-- /aicom-readme-badges -->



> **Ecosystem:** [AICOM overview & live demos](https://modeldev.modelmarket.dev)

**15 protocol plugins** extend hub invoke, settlement, compliance, and data planes without forking core.

**Verifiable math** lives in the separate **[oracles](https://github.com/alexar76/oracles)** monorepo (Platon, Chronos, Murmuration, Lumen, …) — listed on the hub like any capability. **`aimarket-reputation`** scores sellers; **Lumen** oracle supplies PageRank/EigenTrust-style trust artifacts agents can invoke and audit.

## Install

**PyPI (top-5):** [`docs/install.md`](plugins/docs/install.md)

| Package | PyPI status |
|---------|-------------|
| `aimarket-hub` | ✅ 3.0.0 |
| `aimarket-tee`, `aimarket-channels`, `aimarket-reputation` | ✅ 2.0.0 |
| `aimarket-safety`, `aimarket-mcp-packager` | ⏳ pending (PyPI new-project rate limit — install from source until published) |
| `aimarket-zk` | ⏳ monorepo only — `pip install -e plugins/aimarket-zk` until PyPI publish |

**Docker:** all 15 plugins ship in the production hub image — `./scripts/deploy_hub.sh` from the factory monorepo.

**Source:** `pip install -e plugins/aimarket-tee` after cloning this repo.

## TEE Escrow

**Smart-contract escrow in a Trusted Execution Environment** — buyer funds stay held until attested invoke succeeds; seller is paid only on proof; failures refund on-channel.

| | |
|---|---|
| **What** | `aimarket-tee` + provenance + safety → hold → invoke → release/refund |
| **Why** | Micropay scale with **both-side protection** — no human escrow desk |
| **Deep dive** | [docs/killer-feature-tee-escrow.md](plugins/docs/killer-feature-tee-escrow.md) · [Ecosystem capabilities](https://github.com/alexar76/aicom/blob/main/docs/killer-features.md) |

## MCP server — `aimarket-mcp-packager`

One of the 15 plugins is a full **[Model Context Protocol](https://modelcontextprotocol.io) server** — stdio transport, built on the official MCP Python SDK. [`aimarket-mcp-packager`](plugins/aimarket-mcp-packager/) turns any AIMarket capability into a self-hosted MCP product (Docker image, MCP manifest, and a ready `claude_desktop_config.json`) that runs in Claude Desktop, Cursor, Glama, or any stdio MCP client.

| | |
|---|---|
| **Run** | `python mcp_stdio_server.py` (stdio) · `docker build` from repo root |
| **Tools** | `package_capability` · `generate_dockerfile` · `generate_claude_desktop_config` |
| **Registry** | Listed on Glama (score badge above) — see [`aimarket-mcp-packager/`](plugins/aimarket-mcp-packager/) for the server, `glama.json`, and Dockerfile |

## MCP server — `aimarket-oracle-gateway` (standalone repo)

A **second MCP server** ships as its own satellite repo [`alexar76/aimarket-oracle-gateway`](https://github.com/alexar76/aimarket-oracle-gateway) for Glama indexing. [`aimarket-oracle-gateway`](plugins/aimarket-oracle-gateway/) exposes Platon randomness, Chronos VDF, and LUMEN reputation as agent-callable tools — pay-per-call over AIMarket, every result independently verifiable.

| | |
|---|---|
| **Run** | `AIMARKET_HUB_URL=https://modelmarket.dev python mcp_stdio_server.py` |
| **Tools** | `get_random` · `compute_vdf` · `get_reputation_scores` · `list_oracle_capabilities` · … |
| **Registry** | [![aimarket-oracle-gateway MCP server](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway/badges/score.svg)](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway) — [`glama.json`](glama.json) + [`Dockerfile`](Dockerfile) at repo root |

## Plugin index

| Plugin | Role |
|--------|------|
| [aimarket-safety](plugins/aimarket-safety/) | Pre-invoke policy, signed reject |
| [aimarket-tee](plugins/aimarket-tee/) | TEE attestation, escrow hooks |
| [aimarket-provenance](https://github.com/alexar76/aimarket-hub/tree/main/plugins/aimarket-provenance/) | W3C VC invoke receipts |
| [aimarket-reputation](plugins/aimarket-reputation/) | Scores + stake bonds |
| [aimarket-channels](plugins/aimarket-channels/) | USDT channel lifecycle |
| [aimarket-auction](plugins/aimarket-auction/) | Price discovery |
| [aimarket-orchestrator](plugins/aimarket-orchestrator/) | Multi-capability plans |
| [aimarket-data-cap](plugins/aimarket-data-cap/) | Data-capability packaging |
| [aimarket-dataset](plugins/aimarket-dataset/) | Dataset listings |
| [aimarket-mcp-packager](plugins/aimarket-mcp-packager/) | **MCP server** (stdio) — package capabilities as Docker + MCP manifest + Claude Desktop config |
| [aimarket-nft](plugins/aimarket-nft/) | Capability NFTs |
| [aimarket-personas](plugins/aimarket-personas/) | Seller personas |
| [aimarket-promo](plugins/aimarket-promo/) | Promotions |
| [aimarket-streaming](plugins/aimarket-streaming/) | Streaming invoke |
| [aimarket-zk](plugins/aimarket-zk/) | ZK cohort proofs |

Hub loads plugins from [`aimarket-hub/plugins/`](https://github.com/alexar76/aimarket-hub/tree/main/plugins/) at runtime. Monorepo copies under `plugins/` are the source-of-truth for docs and tests.
