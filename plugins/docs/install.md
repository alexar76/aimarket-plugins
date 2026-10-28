# Installing AIMarket Hub plugins

Hub plugins extend invoke, settlement, compliance, and data planes. Install the **hub first**, then plugins.

---

## PyPI (recommended)

**Prerequisite:** `pip install aimarket-hub` (3.0.0+)

### Five core plugins

| Plugin | PyPI | Role |
|--------|------|------|
| `aimarket-tee` | [pypi.org/project/aimarket-tee](https://pypi.org/project/aimarket-tee/) | TEE attestation + escrow hooks |
| `aimarket-channels` | [pypi.org/project/aimarket-channels](https://pypi.org/project/aimarket-channels/) | USDT payment channels |
| `aimarket-reputation` | [pypi.org/project/aimarket-reputation](https://pypi.org/project/aimarket-reputation/) | Stake bonds + reputation |
| `aimarket-safety` | [pypi.org/project/aimarket-safety](https://pypi.org/project/aimarket-safety/) | Pre-invoke safety gate |
| `aimarket-mcp-packager` | [pypi.org/project/aimarket-mcp-packager](https://pypi.org/project/aimarket-mcp-packager/) | MCP server packager |

```bash
pip install aimarket-hub
pip install aimarket-tee aimarket-channels aimarket-reputation aimarket-safety aimarket-mcp-packager
aimarket serve
```

Or install hub with core plugins in one step:

```bash
pip install "aimarket-hub[plugins]"
aimarket serve
```

---

## Docker (all plugins pre-bundled)

Production redeploy from monorepo root:

```bash
./scripts/deploy_hub.sh
```

Image includes all 15 plugins. See [`aimarket-hub/README.md`](https://github.com/alexar76/aimarket-hub/blob/main/README.md).

GHCR (hub-only, install plugins via pip in your own layer):

```bash
docker pull ghcr.io/alexar76/aimarket-hub:latest
```

---

## From source (development)

```bash
git clone https://github.com/alexar76/aimarket-plugins.git
cd aimarket-plugins
pip install -e ../aimarket-hub   # or pip install aimarket-hub
pip install -e plugins/aimarket-tee
pip install -e plugins/aimarket-safety
# …
```

---

## Additional published plugins

The other ten protocol plugins are also available on PyPI. Install only the plugins
you need; they are not included in the Hub's five-plugin `plugins` extra.

```bash
pip install aimarket-auction aimarket-data-cap aimarket-dataset aimarket-nft \
  aimarket-orchestrator aimarket-personas aimarket-promo aimarket-streaming \
  aimarket-zk aimarket-provenance
```

The standalone Oracle Gateway is a separate MCP server, not one of the 15 Hub plugins:

```bash
pip install aimarket-oracle-gateway
# For a federated oracle, copy source_hub from the Hub search result.
AIMARKET_HUB_URL=https://modelmarket.dev \
  AIMARKET_SOURCE_HUB="<source_hub from the selected catalog result>" \
  aimarket-oracle-gateway
```

For the Packager MCP server, use the installed console command. From 2.1.3 it is also
installed under the package's own name, which is what `uvx aimarket-mcp-packager` and
MCP-registry clients run:

```bash
aimarket-mcp-packager-mcp
```

Both MCP commands use stdio and should normally be launched by an MCP client. The
Packager's generators are local; Oracle Gateway's paid tools require an explicitly
configured Hub and payment authorization. Its `list_oracle_capabilities` tool is free
and works without a configured provider.

---

## Verify loaded plugins

```bash
curl -s http://localhost:9083/ai-market/v2/plugins | jq '.plugins | length'
```

Expected: 5+ after installing the top-5 set.
