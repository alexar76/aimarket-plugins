# aimarket-provenance — AI Provenance Layer
#
# AWR/2 work receipts for AI outputs (awr/SPEC.md 2.0.0): W3C Verifiable Credentials
# secured with an eddsa-jcs-2022 Data Integrity proof over RFC 8785 canonical bytes and
# issued by a did:key. Verification needs no network, no registry and no issuer-specific
# software.
#
# Canonicalization, proofs and did:key derivation come from the `awr` package and are
# deliberately not reimplemented here — a second copy of them is what split AWR/1 into two
# incompatible dialects (SPEC.md §4.3, Appendix D).
#
# AWR/1 documents already in storage are still verifiable (SPEC.md §12); AWR/1 is never
# issued (§12 forbids it, and no code path here can produce one).
