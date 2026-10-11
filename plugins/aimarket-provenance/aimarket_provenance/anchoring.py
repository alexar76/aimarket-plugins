"""Anchoring work receipts in HISTOR's receipts log.

A receipt this hub issues is a promise only this hub has signed. Anchoring puts its DIGEST —
never its content — into a public, append-only Merkle log run by someone else (HISTOR, see
``histor/histor/receipts.py``), so that:

* a buyer can later prove the receipt existed at the time it says, to anyone, without trusting
  this hub (an inclusion proof against a head HISTOR signed);
* this hub cannot afterwards deny, backdate or quietly replace a receipt it issued.

What leaves the hub is exactly four facts, signed with the receipts' own key: the receipt's
digest, the issuer's ``did:key``, the issue time, and the anchor type. Not the buyer, the price,
the input or the output.

Delivery is an outbox: issuing a receipt only inserts a row, and a background thread sends
pending rows in batches. HISTOR being down, slow or unreachable therefore never touches an
invoke — the rows wait and the thread retries with backoff. Off unless
``AIMARKET_HISTOR_URL`` is set.

Retention. Once HISTOR holds an anchor, the log itself is the record, so the thread deletes
``anchored`` rows whose ``anchored_at`` is older than ``AIMARKET_HISTOR_OUTBOX_RETAIN_DAYS``
(default 30; 0 or less keeps everything), at most once an hour. ``pending`` rows (not yet
delivered) and ``refused`` rows (the only record of why an anchor failed) are never deleted.
``anchored_at`` is ``datetime('now')`` on SQLite (naive UTC) but ``NOW()`` stored as text in
the SESSION time zone on PostgreSQL, so it is aged in Python by its real instant, never
compared as text in SQL.

Log fallback. The anchor-status route asks with ``status(digest, ask_log=True)``: when the
outbox has no row (a pruned receipt, or one never queued) it asks HISTOR for the proof
(``GET /api/v1/receipts/proof``, 5 s timeout). A proof that names this digest, under this
hub's key, reads as ``anchored`` with ``"source": "log"``; a 404 reads as nothing (the route
says ``not_queued``); an outage or any other answer reads as ``unknown``, never ``anchored``.
Plain ``status(digest)`` stays an outbox lookup: the backfill uses it to mean "already
queued", and must neither call HISTOR per receipt nor take an outage for a queued row.
Only an anchor under this hub's CURRENT key reads as anchored from the log: after a key
rotation, receipts whose rows were pruned read ``unknown`` although HISTOR still holds them
(under the old issuer), which is the honest answer this hub can give about them.
"""
from __future__ import annotations

import base64
import logging
import math
import os
import threading
import time
import urllib.parse
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

ANCHOR_TYPE = "histor.receipt-anchor/v1"
BATCH = 100
# HISTOR accepts an anchor up to 30 days after its issue time; past that no retry can land.
MAX_AGE = timedelta(days=30)
DEFAULT_RETAIN_DAYS = 30.0
PRUNE_EVERY_S = 3600.0
# The route waits on this call while a buyer waits on the route.
LOG_TIMEOUT_S = 5.0
# The status route is public, so without a memory every request for a receipt the outbox has
# no row for would be one more request to HISTOR: anyone could make this hub hammer the log.
# An anchor never changes once logged; "not in the log" and "could not ask" may.
LOG_CACHE_S = {"anchored": 3600.0, "absent": 300.0, "unknown": 30.0}
LOG_CACHE_SIZE = 2048
# Refusals that describe the LOG's state or clock rather than the anchor: an issuer list
# the operator has not updated yet, a clock that ran ahead. The same bytes can be accepted
# later, so these rows stay pending (with backoff) instead of being marked refused for good.
RETRYABLE_REFUSALS = ("issuer is not accepted", "outside the window")

_OUTBOX_DDL = """
    CREATE TABLE IF NOT EXISTS provenance_anchor_outbox (
        receipt_digest TEXT PRIMARY KEY,
        issued_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        leaf_index INTEGER,
        last_error TEXT NOT NULL DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        anchored_at TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_anchor_outbox_status ON provenance_anchor_outbox(status, created_at);
"""


def histor_url() -> str:
    return os.environ.get("AIMARKET_HISTOR_URL", "").strip().rstrip("/")


def retain_days() -> float:
    """Days an anchored row is kept; 0 or less means forever. Read at each prune, so a
    restart is not needed to change it. A value that is not a number keeps everything:
    deleting rows is the one thing a typo must not switch on."""
    raw = os.environ.get("AIMARKET_HISTOR_OUTBOX_RETAIN_DAYS", "").strip()
    if not raw:
        return DEFAULT_RETAIN_DAYS
    try:
        days = float(raw)
    except ValueError:
        logger.warning("anchoring: AIMARKET_HISTOR_OUTBOX_RETAIN_DAYS is not a number; keeping every row")
        return 0.0
    return days if math.isfinite(days) else 0.0


def _instant(value: Any) -> datetime | None:
    """``anchored_at`` as an aware UTC instant, or None when it cannot be read (then kept).

    SQLite's datetime('now') is naive UTC; PostgreSQL's NOW() stored as text carries the
    session's offset ('2026-09-26 22:14:03.123456+03'). Compared as text, a session east of
    UTC ages a row by hours too few and one west of it by hours too many (the lesson of
    aimarket_hub.invoke_funding._age_s)."""
    if isinstance(value, datetime):
        when = value
    else:
        try:
            when = datetime.fromisoformat(str(value or "").strip())
        except ValueError:
            return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_anchor(key: Any, *, receipt_digest: str, issued_at: str) -> dict[str, Any]:
    """Byte-for-byte the document HISTOR verifies (histor.logbook.sign_document): Ed25519 by
    the issuer's did:key over the RFC 8785 bytes of every member except ``signature``."""
    from awr import canonicalize

    body = {"type": ANCHOR_TYPE, "issuer": key.did, "receiptDigest": receipt_digest, "issuedAt": issued_at}
    signature = key.sign(canonicalize(body))
    return {**body, "signature": {
        "alg": "Ed25519",
        "verificationMethod": f"{key.did}#{key.did.split(':')[-1]}",
        "value": _b64url(signature),
    }}


class AnchorOutbox:
    def __init__(self, backend: Any, signing_key: Any, *, url: str = "", flush_s: float = 30.0,
                 post: Any = None, get: Any = None) -> None:
        self._db = backend
        self._key = signing_key
        self.url = (url or histor_url()).rstrip("/")
        self.flush_s = max(1.0, float(flush_s))
        self._post = post  # injectable for tests; defaults to httpx
        self._get = get    # likewise, for the proof lookup
        self._monotonic = time.monotonic
        self._pruned_at: float | None = None
        self._log_cache: OrderedDict[str, tuple[float, dict[str, Any] | None]] = OrderedDict()
        self._log_cache_lock = threading.Lock()
        self._warned_unreadable = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._db.executescript(_OUTBOX_DDL)
        self._db.commit()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def enqueue(self, receipt_digest: str, issued_at: str | None = None) -> None:
        if not self.enabled or not receipt_digest:
            return
        stamp = issued_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._db.execute(
            "INSERT INTO provenance_anchor_outbox (receipt_digest, issued_at) VALUES (?, ?) "
            "ON CONFLICT DO NOTHING",
            (receipt_digest, stamp),
        )
        self._db.commit()

    def _proof_url(self, receipt_digest: str) -> str:
        # The digest is base64 ("sha256-…" with + and /): a raw '+' in a query string
        # decodes as a space, so about half the links would 404.
        return f"{self.url}/api/v1/receipts/proof?digest={urllib.parse.quote(receipt_digest, safe='')}"

    def status(self, receipt_digest: str, *, ask_log: bool = False) -> dict[str, Any] | None:
        """The outbox row's state; with *ask_log*, HISTOR's answer when there is no row."""
        row = self._db.execute(
            "SELECT receipt_digest, status, attempts, leaf_index, last_error, anchored_at "
            "FROM provenance_anchor_outbox WHERE receipt_digest = ?",
            (receipt_digest,),
        ).fetchone()
        if not row:
            return self._cached_log_status(receipt_digest) if ask_log and self.enabled else None
        out = {k: row[k] for k in ("receipt_digest", "status", "attempts", "leaf_index", "anchored_at")}
        if row["last_error"]:
            out["last_error"] = row["last_error"]
        if self.url:
            out["log"] = self.url
            if row["status"] == "anchored":
                out["proof_url"] = self._proof_url(receipt_digest)
        return out

    def _cached_log_status(self, receipt_digest: str) -> dict[str, Any] | None:
        now = self._monotonic()
        with self._log_cache_lock:
            hit = self._log_cache.get(receipt_digest)
            if hit is not None and hit[0] > now:
                self._log_cache.move_to_end(receipt_digest)
                return dict(hit[1]) if hit[1] is not None else None
        result = self._log_status(receipt_digest)
        kind = "absent" if result is None else ("anchored" if result["status"] == "anchored" else "unknown")
        with self._log_cache_lock:
            self._log_cache[receipt_digest] = (now + LOG_CACHE_S[kind], result)
            self._log_cache.move_to_end(receipt_digest)
            while len(self._log_cache) > LOG_CACHE_SIZE:
                self._log_cache.popitem(last=False)
        return dict(result) if result is not None else None

    def _log_status(self, receipt_digest: str) -> dict[str, Any] | None:
        """What HISTOR holds for a digest the outbox no longer (or never) had.

        Only a proof naming this digest, under this hub's key, reads as anchored: a pruned
        row must not turn into "not_queued", and a failed lookup must not turn into a claim.
        """
        proof_url = self._proof_url(receipt_digest)
        try:
            get = self._get
            if get is None:
                import httpx

                def get(url: str) -> Any:
                    return httpx.get(url, timeout=LOG_TIMEOUT_S)
            response = get(proof_url)
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise RuntimeError(f"HISTOR answered {response.status_code}")
            proof = response.json()
            anchor = proof.get("anchor") if isinstance(proof, dict) else None
            if not isinstance(anchor, dict) or anchor.get("receiptDigest") != receipt_digest:
                raise RuntimeError("HISTOR's proof names a different receipt")
            # A digest someone else anchored first is in the log, but not as this hub's word.
            issuer = getattr(self._key, "did", None)
            if issuer and anchor.get("issuer") != issuer:
                raise RuntimeError("HISTOR holds this digest under another issuer")
            leaf_index = proof.get("leaf_index")
            if isinstance(leaf_index, bool) or not isinstance(leaf_index, int) or leaf_index < 0:
                raise RuntimeError("HISTOR's proof carries no leaf index")
        except Exception as exc:
            return {"receipt_digest": receipt_digest, "status": "unknown", "log": self.url,
                    "last_error": (str(exc) or type(exc).__name__)[:200]}
        return {"receipt_digest": receipt_digest, "status": "anchored", "leaf_index": leaf_index,
                "log": self.url, "proof_url": proof_url, "source": "log"}

    def prune(self, now: datetime | None = None) -> int:
        """Delete anchored rows older than the retention; return how many went.

        Never a pending row (undelivered) or a refused one (the only record of why). The
        status is re-checked in the DELETE itself, so a row that changed after it was read
        is left alone."""
        days = retain_days()
        if days <= 0:
            return 0
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        cutoff = now - timedelta(days=days)
        rows = self._db.execute(
            "SELECT receipt_digest, anchored_at FROM provenance_anchor_outbox WHERE status = 'anchored'"
        ).fetchall()
        stale: list[str] = []
        unreadable = 0
        for r in rows:
            when = _instant(r["anchored_at"])
            if when is None:
                unreadable += 1
            elif when < cutoff:
                stale.append(r["receipt_digest"])
        if rows and unreadable == len(rows) and not self._warned_unreadable:
            # Kept, which is safe — but a database writing times in a non-ISO DateStyle would
            # make retention silently do nothing, forever. Say so once.
            self._warned_unreadable = True
            logger.warning("anchoring: none of %d anchored row times could be read (%r); "
                           "outbox retention cannot age them", len(rows), rows[0]["anchored_at"])
        removed = 0
        for digest in stale:
            cursor = self._db.execute(
                "DELETE FROM provenance_anchor_outbox WHERE receipt_digest = ? AND status = 'anchored'",
                (digest,),
            )
            count = getattr(cursor, "rowcount", None)
            removed += count if isinstance(count, int) and count >= 0 else 1
        if stale:
            self._db.commit()
        return removed

    def flush_once(self) -> dict[str, int]:
        """Send one batch. Returns counts; never raises."""
        counts = {"sent": 0, "anchored": 0, "refused": 0, "deferred": 0, "failed": 0}
        if not self.enabled:
            return counts
        # Fewest attempts first: a row that keeps failing must not starve fresh ones, and no
        # attempt count retires a row — an outage of any length ends in delivery (or, past
        # HISTOR's 30-day window, in a refusal that says so).
        rows = self._db.execute(
            "SELECT receipt_digest, issued_at FROM provenance_anchor_outbox "
            "WHERE status = 'pending' ORDER BY attempts, created_at LIMIT ?",
            (BATCH,),
        ).fetchall()
        if not rows:
            return counts
        anchors = [sign_anchor(self._key, receipt_digest=r["receipt_digest"], issued_at=r["issued_at"]) for r in rows]
        counts["sent"] = len(anchors)
        try:
            post = self._post
            if post is None:
                import httpx

                def post(url: str, payload: dict) -> Any:
                    return httpx.post(url, json=payload, timeout=20.0)
            response = post(f"{self.url}/api/v1/receipts/anchors", {"anchors": anchors})
            if response.status_code != 200:
                raise RuntimeError(f"HISTOR answered {response.status_code}: {str(response.text)[:200]}")
            results = response.json().get("results") or []
        except Exception as exc:
            for r in rows:
                self._db.execute(
                    "UPDATE provenance_anchor_outbox SET attempts = attempts + 1, last_error = ? "
                    "WHERE receipt_digest = ? AND status = 'pending'",
                    (str(exc)[:300], r["receipt_digest"]),
                )
            self._db.commit()
            counts["failed"] = len(rows)
            logger.warning("anchoring: HISTOR unreachable, %d receipt(s) wait: %s", len(rows), exc)
            return counts
        issued = {r["receipt_digest"]: r["issued_at"] for r in rows}
        for result in results:
            digest = result.get("receipt_digest")
            reason = str(result.get("reason") or "refused")[:300]
            if result.get("status") not in ("logged", "duplicate") and self._retryable(reason, issued.get(digest)):
                self._db.execute(
                    "UPDATE provenance_anchor_outbox SET attempts = attempts + 1, last_error = ? "
                    "WHERE receipt_digest = ? AND status = 'pending'",
                    (reason, digest),
                )
                counts["deferred"] += 1
                continue
            if result.get("status") in ("logged", "duplicate"):
                self._db.execute(
                    "UPDATE provenance_anchor_outbox SET status = 'anchored', leaf_index = ?, "
                    "anchored_at = datetime('now'), last_error = '' WHERE receipt_digest = ?",
                    (result.get("leaf_index"), digest),
                )
                counts["anchored"] += 1
            else:
                # Any other refusal is a verdict about this anchor (its signature, its shape,
                # a digest another issuer anchored first): retrying the same signed bytes
                # cannot change it. Kept visible, not retried.
                self._db.execute(
                    "UPDATE provenance_anchor_outbox SET status = 'refused', last_error = ? "
                    "WHERE receipt_digest = ?",
                    (reason, digest),
                )
                counts["refused"] += 1
        self._db.commit()
        if counts["deferred"]:
            logger.warning("anchoring: HISTOR deferred %d receipt(s) (%s)", counts["deferred"],
                           next((r.get("reason") for r in results if r.get("status") == "refused"), ""))
        return counts

    @staticmethod
    def _retryable(reason: str, issued_at: str | None) -> bool:
        if not any(marker in reason for marker in RETRYABLE_REFUSALS):
            return False
        try:
            when = datetime.strptime(issued_at or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        except ValueError:
            return False
        return datetime.now(UTC) - when < MAX_AGE

    # -- the background sender -------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="provenance-anchor-outbox", daemon=True)
        self._thread.start()
        logger.info("anchoring receipts to %s every %.0fs", self.url, self.flush_s)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _prune_if_due(self) -> None:
        # Due on the first tick, not an hour after start: a hub restarted more often than
        # hourly (every deploy) would otherwise never prune at all.
        now = self._monotonic()
        if self._pruned_at is not None and now - self._pruned_at < PRUNE_EVERY_S:
            return
        self._pruned_at = now   # set first: a prune that keeps failing still runs hourly
        try:
            removed = self.prune()
        except Exception as exc:
            logger.error("anchoring: outbox prune raised: %s", exc)
            return
        if removed:
            logger.info("anchoring: pruned %d anchored row(s) past retention", removed)

    def _run(self) -> None:
        delay = self.flush_s
        while not self._stop.wait(delay):
            try:
                counts = self.flush_once()
            except Exception as exc:
                logger.error("anchoring: flush raised: %s", exc)
                counts = {"failed": 1, "sent": 0}
            self._prune_if_due()
            # Back off while HISTOR is failing or deferring, return to the normal pace once it
            # takes the batch.
            delay = min(delay * 2, 600.0) if counts.get("failed") or counts.get("deferred") else self.flush_s
