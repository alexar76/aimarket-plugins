# Security Policy — aimarket-oracle-gateway

## Reporting a Vulnerability

**Do not open a public issue for security bugs.**

Email: **security@aicom.io**

We acknowledge within 48 hours and share a fix timeline.

## Scope

- The MCP tool surface and the gateway routing/parse logic (`gateway_core.py`).
- AIMarket v2 invoke calls, payment-channel headers, and bearer-token handling.

## Design notes (by construction)

- **No custody.** The gateway holds no keys and no funds; payment settles on-chain via the
  AIMarket escrow (the gateway only forwards an `X-Payment-Channel` id if you provide one).
- **Fails closed.** With neither `AIMARKET_HUB_URL` nor `AIMARKET_ORACLE_URL` set, every tool
  raises with a clear message — it never fabricates or simulates an oracle result.
- **Verify, don't trust.** Randomness carries an Ed25519 signature and a proof; VDF output carries
  a Wesolowski proof — verify them (`verify_random` / `verify_vdf`) rather than trusting the value.

## Out of Scope

- Third-party dependencies (report upstream).
- The oracle implementations themselves (reported via the oracle subprojects).
- Social engineering; issues requiring physical access to user hardware.

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest main | yes |
| older tags | best effort |
