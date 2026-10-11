"""Queue receipts issued before anchoring was switched on for HISTOR's receipts log.

Anchoring (anchoring.py) only queues receipts as they are issued. A hub that turned it on
later has older receipts nobody anchored. This walks the stored receipts and queues each
one that HISTOR can still accept, with its OWN issue time; the running hub's outbox thread
then sends them like any other row. Nothing is signed or sent here.

What a backfilled anchor proves is narrower than a live one, and that is stated, not hidden:
HISTOR records when it logged the anchor, so the inclusion proof shows the receipt existed
by that moment — not at the issue time the issuer claims.

HISTOR accepts an anchor only while its issuedAt is at most 30 days old (histor/receipts.py
MAX_AGE): the window is what stops an issuer from backdating. A receipt older than that
cannot be anchored with its true time, and anchoring it with a false one would defeat the
log, so it is counted and left alone.

Queued only when the receipt:
  * verifies (its own proof), and was issued by THIS hub's provenance key — the anchor says
    "this issuer issued this digest", which is true of nothing else;
  * has a readable issue time inside the window, with a margin so it cannot expire between
    being queued and being sent;
  * is not already in the outbox.

    python -m aimarket_provenance.backfill --db /app/data/provenance.db --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .anchoring import MAX_AGE, AnchorOutbox, histor_url

if TYPE_CHECKING:
    from collections.abc import Iterable

# Queued rows go out within a flush or two (30 s each, 100 per batch); an hour of margin
# covers a hub whose HISTOR is briefly unreachable when the backfill runs.
DEFAULT_MARGIN = timedelta(hours=1)
# HISTOR refuses an issuedAt in the future; a receipt stamped ahead of this clock is skipped.
FUTURE_SKEW = timedelta(minutes=5)

COUNTS = ("seen", "queued", "already_queued", "too_old", "future", "foreign_issuer",
          "invalid", "unreadable")


def _issued_at(stamp: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(stamp or "").strip())
    except ValueError:
        return None
    if when.tzinfo is None:
        return None          # a receipt time without an offset is not a time HISTOR can check
    return when.astimezone(UTC).replace(microsecond=0)


def backfill(receipts: Iterable[Any], outbox: AnchorOutbox, *, issuer_public_key_b64: str,
             now: datetime | None = None, margin: timedelta = DEFAULT_MARGIN,
             dry_run: bool = False) -> dict[str, int]:
    """Queue every eligible receipt in *receipts*; return what happened to each, by count."""
    counts = dict.fromkeys(COUNTS, 0)
    now = (now or datetime.now(UTC)).astimezone(UTC)
    oldest = now - (MAX_AGE - margin)
    for receipt in receipts:
        counts["seen"] += 1
        try:
            digest = receipt.digest_sri
            issuer = receipt.issuer_public_key_b64
            stamp = receipt.timestamp
        except Exception:
            counts["unreadable"] += 1
            continue
        if not issuer or issuer != issuer_public_key_b64:
            counts["foreign_issuer"] += 1
            continue
        when = _issued_at(stamp)
        if when is None or not digest:
            counts["unreadable"] += 1
            continue
        if when < oldest:
            counts["too_old"] += 1
            continue
        if when > now + FUTURE_SKEW:
            counts["future"] += 1
            continue
        if outbox.status(digest) is not None:
            counts["already_queued"] += 1
            continue
        try:
            valid = receipt.verify()
        except Exception:
            valid = False
        if not valid:
            counts["invalid"] += 1
            continue
        if not dry_run:
            outbox.enqueue(digest, when.strftime("%Y-%m-%dT%H:%M:%SZ"))
        counts["queued"] += 1
    return counts


def stored_receipts(storage: Any, page: int = 200) -> Iterable[Any]:
    """Every stored receipt, oldest first, a page at a time (rehydrated from stored bytes)."""
    offset = 0
    while True:
        rows = storage._conn.execute(
            "SELECT raw_json FROM provenance_receipts ORDER BY timestamp, id LIMIT ? OFFSET ?",
            (page, offset),
        ).fetchall()
        if not rows:
            return
        for row in rows:
            try:
                yield storage._load(row["raw_json"])
            except Exception:
                yield _Unreadable()
        offset += len(rows)


class _Unreadable:
    @property
    def digest_sri(self) -> str:
        raise ValueError("stored receipt could not be parsed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m aimarket_provenance.backfill", description=__doc__.split("\n")[0])
    parser.add_argument("--db", default=os.environ.get("AIMARKET_PROVENANCE_DB_PATH", "data/provenance.db"),
                        help="the provenance SQLite file the hub uses (ignored with DATABASE_URL)")
    parser.add_argument("--key", default=None,
                        help="the provenance signing key file (default: AIMARKET_PROVENANCE_KEY_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="count what would be queued; queue nothing")
    args = parser.parse_args(argv)

    url = histor_url()
    if not url:
        # The only guard a bubble or a private hub has against publishing its receipts: the
        # same switch that keeps its live receipts out of the public log keeps these out too.
        print("AIMARKET_HISTOR_URL is not set: this hub does not anchor, so nothing is queued.",
              file=sys.stderr)
        return 2
    from aimarket_hub.signing import Signer

    from .plugin import DEFAULT_SIGNING_KEY_PATH
    from .storage import ProvenanceStorage

    key_path = args.key or os.environ.get("AIMARKET_PROVENANCE_KEY_PATH", DEFAULT_SIGNING_KEY_PATH)
    if not os.path.exists(key_path):
        print(f"no provenance key at {key_path}", file=sys.stderr)
        return 2
    public_key_b64 = Signer(key_path=key_path).public_key_b64
    storage = ProvenanceStorage(args.db, database_url=os.environ.get("DATABASE_URL", ""))
    # The outbox is only written to: its sender thread is never started here, the hub's is.
    outbox = AnchorOutbox(storage._backend, signing_key=None, url=url)
    counts = backfill(stored_receipts(storage), outbox, issuer_public_key_b64=public_key_b64,
                      dry_run=args.dry_run)
    print(json.dumps({"log": url, "dry_run": args.dry_run, **counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
