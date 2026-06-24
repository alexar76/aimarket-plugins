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

**One MCP server. Eleven oracle families. Pay-per-call.**

Transport: **stdio** (`mcp_stdio_server.py`). Built with the official
**Model Context Protocol** Python SDK ([`mcp`](https://github.com/modelcontextprotocol/python-sdk), `FastMCP`).
Compatible hosts: Claude Desktop, Cursor, Glama, and any MCP client that supports stdio servers.

| Item | Location |
|------|----------|
| MCP entrypoint | [`mcp_stdio_server.py`](mcp_stdio_server.py) |
| Tool routing core | [`aimarket_oracle_gateway/gateway_core.py`](aimarket_oracle_gateway/gateway_core.py) |
| Extended tool surface | [`aimarket_oracle_gateway/mcp_tool_surface.py`](aimarket_oracle_gateway/mcp_tool_surface.py) |
| Glama / Docker run | [`Dockerfile`](Dockerfile), [`glama.json`](glama.json) |

---

A **passthrough MCP server** — the discovery + consumption storefront for the entire
[oracle-as-a-service specs](../../docs/specs/README.md). One server, **21 tools**, every result
**independently verifiable** (signatures, VDF proofs, classical certificates, deterministic replay).

## Tools (21)

| Category | MCP tool | capabilityId | price |
|----------|----------|--------------|-------|
| **Randomness** | `get_random` | `platon.random@v1` | $0.004 |
| | `get_randomness_beacon` | `platon.beacon@v1` | $0.004 |
| | `ask_oracle` | `platon.ask@v1` | $0.003 |
| | `verify_random` | `platon.verify@v1` | $0.001 |
| **Delay** | `compute_vdf` | `chronos.eval@v1` | $0.01 |
| | `verify_vdf` | `chronos.verify@v1` | $0.001 |
| **Reputation** | `get_reputation_scores` | `lumen.reputation@v1` | $0.005 |
| | `get_agent_trust` | `lumen.score@v1` | $0.003 |
| | `verify_reputation` | `lumen.verify@v1` | $0.002 |
| **Consensus** | `aggregate_values` | `murmuration.aggregate@v1` | $0.002 |
| **Thermodynamics** | `audit_compute_cost` | `landauer.audit@v1` | $0.01 |
| | `verify_compute_cost` | `landauer.verify@v1` | $0.001 |
| **Routing** | `compute_least_time_route` | `fermat.route@v1` | $0.01 |
| | `verify_least_time_route` | `fermat.verify@v1` | $0.001 |
| **Cascade risk** | `analyze_cascade_risk` | `ablation.cascade@v1` | $0.01 |
| | `verify_cascade_risk` | `ablation.verify@v1` | $0.001 |
| **Sampling** | `get_quasirandom_sequence` | `lattice.sequence@v1` | $0.002 |
| | `get_blue_noise` | `turing.bluenoise@v1` | $0.002 |
| **Optimization** | `optimize_route` | `colony.optimize@v1` | $0.005 |
| **Resilience** | `analyze_network_resilience` | `percola.threshold@v1` | $0.01 |
| | `verify_network_resilience` | `percola.verify@v1` | $0.001 |
| **Discovery** | `list_oracle_capabilities` | — | free |

### Subscription tiers (optional Hub bundles)

High-volume tools also advertise monthly bundles in `list_oracle_capabilities`:

| Tool | Starter | Pro |
|------|---------|-----|
| `aggregate_values` | 10k calls / $10 | 100k / $50 |
| `get_reputation_scores` | 5k / $20 | 50k / $100 |
| `audit_compute_cost` | 10k / $15 | 100k / $75 |
| `compute_least_time_route` | 10k / $25 | 100k / $120 |
| `analyze_cascade_risk` | 5k / $50 | 50k / $250 |

Pay-per-call remains the default; bundles are Hub-side packaging.

## Configure (env)

| var | meaning |
|-----|---------|
| `AIMARKET_HUB_URL` | AIMarket Hub base URL — **recommended**: metered + paid |
| `AIMARKET_ORACLE_URL` | direct oracle-family URL (if no hub) — demo/free path |
| `AIMARKET_PAYMENT_CHANNEL` | optional pre-opened payment channel id |
| `AIMARKET_PAYMENT_CHANNEL_SECRET` | per-channel debit secret |
| `AIMARKET_API_TOKEN` | optional bearer token |
| `AIMARKET_MAX_PER_CALL_USD` | hard cap per call (default `0.10`) |
| `AIMARKET_MAX_SPEND_USD` | hard cumulative budget (default `5.0`) |
| `AIMARKET_PRICE_TOLERANCE` | reject overcharge vs advertised (default `0.10`) |

If neither URL is set the server fails closed with a clear message.

**Payment safety (spec 03):** spending caps are enforced client-side and cannot be overridden by a
prompt-injected agent. See [docs/specs/03-mcp-payment-and-security.md](../../docs/specs/03-mcp-payment-and-security.md).

## Run

```bash
pip install -r requirements-mcp.txt && pip install --no-deps -e .
AIMARKET_HUB_URL=https://modelmarket.dev python mcp_stdio_server.py
```

Claude Desktop (`mcpServers` entry):

```json
{ "mcpServers": { "aimarket-oracle-gateway": {
  "command": "python", "args": ["mcp_stdio_server.py"],
  "env": { "AIMARKET_HUB_URL": "https://modelmarket.dev" } } } }
```

## Publish on Glama

[![aimarket-oracle-gateway MCP server](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway/badges/score.svg)](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway)

Listing: **[glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway](https://glama.ai/mcp/servers/alexar76/aimarket-oracle-gateway)**

## Verifiability

- **Platon** — Ed25519 signature over `(random_hex ‖ proof)`
- **Chronos** — Wesolowski VDF proof, cheap `verify_vdf`
- **LUMEN** — re-derive PageRank from committed graph
- **Murmuration** — deterministic re-aggregation from input values
- **Landauer** — bit-for-bit erasure count from ops DAG + `circuit_commitment`
- **Fermat** — dual certificate `T(v)` checked in O(E)
- **Ablation** — sandpile replay, order-independent `topple_total` + `tau`
- **Lattice / Turing** — deterministic from `(count, dim, skip)` / `seed`
- **Colony** — admissible `lower_bound` + `gap` certificate
- **Percola** — recomputable percolation sweep + `f_c`

## Tests

```bash
PYTHONPATH=. pytest tests/
```
