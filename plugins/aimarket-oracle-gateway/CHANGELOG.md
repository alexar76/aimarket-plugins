# Changelog

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
