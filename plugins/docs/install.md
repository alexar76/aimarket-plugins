# Installing AIMarket Hub plugins

Hub plugins extend invoke, settlement, compliance, and data planes. Install the **hub first**, then plugins.

---

## PyPI (recommended)

**Prerequisite:** `pip install aimarket-hub` (3.0.0+)

### Top-5 published plugins

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

## Remaining plugins

Other plugins (`aimarket-auction`, `aimarket-nft`, …) are **source / Docker only** until published to PyPI. Track [GitHub Releases](https://github.com/alexar76/aimarket-plugins/releases) for new PyPI packages.

---

## Verify loaded plugins

```bash
curl -s http://localhost:9083/ai-market/v2/plugins | jq '.plugins | length'
```

Expected: 5+ after installing the top-5 set.
