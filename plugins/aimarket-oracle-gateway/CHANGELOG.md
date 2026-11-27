# Changelog

## 0.4.0 — 2026-11-26

- Answer MCP 2026-07-28 beside the handshake revisions. A 2026-07-28 client sends no
  `initialize`: it asks `server/discover` and names its protocol version in every
  request. Clients that open with `initialize` are answered as before.
- Build the server on `MCPServer` from the 2.x `mcp` SDK instead of `FastMCP`; the
  dependency is now `mcp>=2.3.0,<3`. The list of 36 tools a client receives is
  byte-identical to 0.3.3 (compared over stdio against the 1.30 SDK).

## 0.3.3 — 2026-10-28

- Send `max_price_usd` to the Hub before execution, bounded by the per-call cap,
  remaining process budget and advertised-price tolerance.
- Preserve an explicit zero response price instead of substituting a paid hint.
- Enforce the actual per-call cap separately from quote tolerance.
- Default `source_hub` to the protocol's `local` route and accept the selected
  federated source through `AIMARKET_SOURCE_HUB`.
- Document the published install command and the limits of response-side checks
  for direct providers. A strict ceiling may refuse a cent-rounded Hub quote;
  paid fulfillment and a direct-provider atomic payment contract are not claimed.
