"""Provenance receipt storage — SQLite or PostgreSQL via DATABASE_URL.

Follows the DBBackend abstraction for dialect-agnostic queries.
Uses a separate database file/namespace (provenance) to avoid schema coupling.

AWR/1 → AWR/2 row migration
---------------------------

The ``provenance_receipts`` table is **unchanged**: same columns, same indexes, same hub
migration (``005_provenance_receipts``).  No column was added, renamed or dropped, so a
database written by the AWR/1 plugin is read by this one and vice versa, and no data
migration has to run before a hub starts.

Four columns keep their name and meaning while the *encoding* of new rows changes, because
the AWR/2 document states those values differently (``awr/SPEC.md`` §3.2, §5.1, §6.1).
Existing rows are **not** rewritten: SPEC.md §12.2 forbids re-signing historical documents
as AWR/2 ("the issuer cannot honestly re-attest a ``created`` timestamp"), and rewriting
the derived columns while leaving ``raw_json`` at AWR/1 would make the row disagree with
the document it summarises.  Both encodings therefore coexist, and which one a row uses is
readable from the row itself — ``raw_json``'s ``proof.type`` is ``DataIntegrityProof`` for
AWR/2 and ``Ed25519Signature2018`` for AWR/1.

======================  ==========================  ===================================
column                  AWR/1 rows (unchanged)      AWR/2 rows
======================  ==========================  ===================================
``input_hash``          64-char hex SHA-256         SRI string, ``sha256-<base64>`` (§3.2)
``output_hash``         64-char hex SHA-256         SRI string, ``sha256-<base64>`` (§3.2)
``issuer_pubkey_b64``   key asserted by the doc     key **derived** from the ``did:key``
``proof_value``         base64 signature            multibase base58btc, ``z…`` (§6.1)
``parent_receipts``     JSON array of id strings    JSON array of digest references
======================  ==========================  ===================================

``issuer_pubkey_b64`` is worth spelling out: in AWR/1 the embedded key was a second,
independent assertion that could disagree with ``issuer.id`` — and did, since ``issuer.id``
was ``did:key:`` plus 32 base64 characters and named no key at all (Appendix D).  For an
AWR/2 row the column is *computed from* ``issuer.id``, so it is a convenience index, never
an authority.  ``parent_receipts`` gains the parent's ``digestSRI`` alongside its ``id``:
an id-only edge is re-pointable (§13.1), so storing only ids would discard the one part of
the edge that makes it sound.

``price_usd REAL`` remains a float column for querying.  The signed value is the decimal
**string** ``price.amount`` in ``raw_json`` (§4.3 forbids a JSON float inside a signed
document), and any comparison that matters must use that string, not this column.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aimarket_hub.db_backend import create_backend
from aimarket_hub.migrations import Migrations

from .receipt import ProvenanceReceipt


class ProvenanceStorage:
    """Stores and retrieves ProvenanceReceipts — SQLite or PostgreSQL.

    Args:
        db_path: SQLite path (used when database_url is unset)
        database_url: PostgreSQL connection string (optional)
    """

    def __init__(
        self,
        db_path: str | Path = "data/provenance.db",
        database_url: str = "",
    ):
        self.db_path = Path(db_path)
        self._backend = create_backend(
            database_url=database_url, db_path=db_path,
        )
        Migrations(self._backend).apply(target_version=5)
        self._conn = self._backend  # backward compat alias

    def _migrate(self) -> None:
        pass  # Handled by Migrations in __init__

    def _legacy_migrate(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS provenance_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id TEXT NOT NULL UNIQUE,
                model_id TEXT NOT NULL,
                provider_hub TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                output_hash TEXT NOT NULL,
                parent_receipts TEXT DEFAULT '[]',
                timestamp TEXT NOT NULL,
                issuer_pubkey_b64 TEXT NOT NULL,
                proof_value TEXT NOT NULL,
                tee_attestation TEXT,
                latency_ms INTEGER DEFAULT 0,
                price_usd REAL DEFAULT 0.0,
                invocation_nonce TEXT DEFAULT '',
                reputation_score REAL,
                raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_prov_receipt_id
                ON provenance_receipts(receipt_id);
            CREATE INDEX IF NOT EXISTS idx_prov_model
                ON provenance_receipts(model_id);
            CREATE INDEX IF NOT EXISTS idx_prov_provider
                ON provenance_receipts(provider_hub);
            CREATE INDEX IF NOT EXISTS idx_prov_timestamp
                ON provenance_receipts(timestamp);
        """)
        self._conn.commit()

    def store(self, receipt: ProvenanceReceipt) -> None:
        """Store a receipt. Raises sqlite3.IntegrityError on duplicate receipt_id.

        ``raw_json`` holds the document **verbatim**.  It is the only column verification
        ever reads, because SPEC.md §4.2 forbids re-canonicalizing a document through a
        lossy intermediate representation — "a typed struct that drops unknown fields, a
        map that coerces integers to floats, a database column" — and every other column
        here is exactly such a projection.
        """
        raw = receipt.to_json(indent=2, ensure_ascii=False)
        self._conn.execute(
            """INSERT INTO provenance_receipts
               (receipt_id, model_id, provider_hub, input_hash, output_hash,
                parent_receipts, timestamp, issuer_pubkey_b64, proof_value,
                tee_attestation, latency_ms, price_usd, invocation_nonce,
                reputation_score, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                receipt.receipt_id,
                receipt.model_id,
                receipt.provider_hub,
                receipt.input_hash,
                receipt.output_hash,
                json.dumps(
                    receipt.parents if not receipt.is_legacy else receipt.parent_receipts
                ),
                receipt.timestamp,
                receipt.issuer_public_key_b64,
                receipt.proof_value,
                json.dumps(receipt.tee_attestation) if receipt.tee_attestation else None,
                receipt.latency_ms,
                receipt.price_usd,
                receipt.invocation_nonce,
                receipt.reputation_score,
                raw,
            ),
        )
        self._conn.commit()

    def get_by_receipt_id(self, receipt_id: str) -> ProvenanceReceipt | None:
        row = self._conn.execute(
            "SELECT raw_json FROM provenance_receipts WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if not row:
            return None
        return self._load(row["raw_json"])

    def list_receipts(
        self,
        model_id: str | None = None,
        provider_hub: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProvenanceReceipt]:
        conditions: list[str] = []
        params: list[Any] = []
        if model_id:
            conditions.append("model_id = ?")
            params.append(model_id)
        if provider_hub:
            conditions.append("provider_hub = ?")
            params.append(provider_hub)
        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self._conn.execute(
            f"SELECT raw_json FROM provenance_receipts "
            f"WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [self._load(r["raw_json"]) for r in rows]

    def count_receipts(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM provenance_receipts"
        ).fetchone()[0]

    def close(self) -> None:
        self._backend.close()

    # ── internals ───────────────────────────────────────────────

    @staticmethod
    def _load(raw_json: Any) -> ProvenanceReceipt:
        """Rehydrate from the stored text, not from a re-parsed object graph.

        ``from_json`` keeps the bytes so the verifier can apply the checks SPEC.md defines
        over received bytes rather than over a parsed value: the lexical number rule of
        §4.3 and the duplicate-property rule of §4.1 are both unrecoverable after a
        permissive parse.
        """
        return ProvenanceReceipt.from_json(raw_json)
