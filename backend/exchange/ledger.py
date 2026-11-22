"""Append-only, integer-unit double-entry ledger for the closed test exchange.

Amounts are opaque integer ledger units. This module deliberately does not map
those units to KEN decimals or any on-chain asset precision.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Iterable, Mapping

MAX_INT64 = (1 << 63) - 1
GENESIS_HASH = "0" * 64


class LedgerError(RuntimeError):
    """Raised when the ledger invariants would be violated."""


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class DoubleEntryLedger:
    """SQLite-backed immutable journals, serialized with BEGIN IMMEDIATE."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = os.fspath(db_path)
        if self.db_path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self.db_path))
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(
            self.db_path, timeout=30, check_same_thread=False, isolation_level=None
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        if self.db_path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = FULL")
        self._create_schema()
        self.verify()

    def _create_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_transactions (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    created_ns INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    ledger_seq INTEGER NOT NULL REFERENCES ledger_transactions(seq),
                    account TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    amount_units INTEGER NOT NULL CHECK(amount_units != 0),
                    UNIQUE(ledger_seq, account, asset)
                );
                CREATE INDEX IF NOT EXISTS ledger_entries_account_asset
                    ON ledger_entries(account, asset);
                CREATE INDEX IF NOT EXISTS ledger_entries_ledger_seq
                    ON ledger_entries(ledger_seq);
                CREATE TRIGGER IF NOT EXISTS ledger_transactions_no_update
                    BEFORE UPDATE ON ledger_transactions BEGIN
                        SELECT RAISE(ABORT, 'ledger transactions are immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS ledger_transactions_no_delete
                    BEFORE DELETE ON ledger_transactions BEGIN
                        SELECT RAISE(ABORT, 'ledger transactions are immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS ledger_entries_no_update
                    BEFORE UPDATE ON ledger_entries BEGIN
                        SELECT RAISE(ABORT, 'ledger entries are immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS ledger_entries_no_delete
                    BEFORE DELETE ON ledger_entries BEGIN
                        SELECT RAISE(ABORT, 'ledger entries are immutable');
                    END;
                """
            )

    @contextmanager
    def transaction(self):
        """Begin a serialized write transaction; nested transactions are forbidden."""
        with self._lock:
            if self.conn.in_transaction:
                raise LedgerError("nested ledger transaction is not supported")
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.rollback()
                raise
            else:
                self.conn.commit()

    def balance(self, account: str, asset: str) -> int:
        with self._lock:
            rows = self.conn.execute(
                "SELECT amount_units FROM ledger_entries WHERE account = ? AND asset = ?",
                (account, asset),
            ).fetchall()
            return sum(int(row[0]) for row in rows)

    def balances_for_account(self, account: str) -> dict[str, int]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT asset, amount_units FROM ledger_entries WHERE account = ?",
                (account,),
            ).fetchall()
            totals: dict[str, int] = defaultdict(int)
            for row in rows:
                totals[str(row["asset"])] += int(row["amount_units"])
            return {asset: amount for asset, amount in totals.items() if amount}

    def post(
        self,
        event_id: str,
        event_type: str,
        reference: str,
        entries: Iterable[tuple[str, str, int]],
        metadata: Mapping | None = None,
    ) -> dict:
        """Append one balanced journal. Caller must hold ``transaction()``."""
        if not self.conn.in_transaction:
            raise LedgerError("ledger.post requires an active transaction")
        if not event_id or len(event_id) > 200:
            raise LedgerError("event_id must contain 1..200 characters")
        if not event_type or not reference:
            raise LedgerError("event_type and reference are required")
        if self.conn.execute(
            "SELECT 1 FROM ledger_transactions WHERE event_id = ?", (event_id,)
        ).fetchone():
            raise LedgerError(f"duplicate ledger event_id: {event_id}")

        grouped: dict[tuple[str, str], int] = defaultdict(int)
        for account, asset, amount in entries:
            if not isinstance(account, str) or not account or len(account) > 200:
                raise LedgerError("ledger account is invalid")
            if not isinstance(asset, str) or not asset or len(asset) > 32:
                raise LedgerError("ledger asset is invalid")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
                raise LedgerError("ledger amounts must be non-zero integers")
            if abs(amount) > MAX_INT64:
                raise LedgerError("ledger amount exceeds SQLite integer range")
            grouped[(account, asset)] += amount
            if abs(grouped[(account, asset)]) > MAX_INT64:
                raise LedgerError("combined ledger amount exceeds SQLite integer range")

        normalized = [
            (account, asset, amount)
            for (account, asset), amount in grouped.items()
            if amount
        ]
        if not normalized:
            raise LedgerError("a journal must contain non-zero entries")
        by_asset: dict[str, int] = defaultdict(int)
        by_asset_accounts: dict[str, set[str]] = defaultdict(set)
        for account, asset, amount in normalized:
            by_asset[asset] += amount
            by_asset_accounts[asset].add(account)
            existing = self.balance(account, asset)
            resulting = existing + amount
            if abs(resulting) > MAX_INT64:
                raise LedgerError("resulting account balance exceeds SQLite integer range")
            if account.startswith("user:") and account.endswith((":available", ":reserved")) and resulting < 0:
                raise LedgerError("user available/reserved balance cannot be negative")
        for asset, total in by_asset.items():
            if total != 0:
                raise LedgerError(f"unbalanced journal for {asset}: net {total}")
            if len(by_asset_accounts[asset]) < 2:
                raise LedgerError(f"journal for {asset} must move between distinct accounts")

        normalized.sort(key=lambda item: (item[1], item[0], item[2]))
        metadata_obj = dict(metadata or {})
        metadata_json = _canonical_json(metadata_obj)
        created_ns = time.time_ns()
        previous = self.conn.execute(
            "SELECT entry_hash FROM ledger_transactions ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous[0]) if previous else GENESIS_HASH
        hash_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "reference": reference,
            "created_ns": created_ns,
            "metadata": metadata_obj,
            "previous_hash": previous_hash,
            "entries": normalized,
        }
        entry_hash = hashlib.sha256(_canonical_json(hash_payload).encode("utf-8")).hexdigest()
        cursor = self.conn.execute(
            """INSERT INTO ledger_transactions
               (event_id, event_type, reference, created_ns, metadata_json, previous_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_id, event_type, reference, created_ns, metadata_json, previous_hash, entry_hash),
        )
        ledger_seq = int(cursor.lastrowid)
        self.conn.executemany(
            "INSERT INTO ledger_entries(ledger_seq, account, asset, amount_units) VALUES (?, ?, ?, ?)",
            [(ledger_seq, account, asset, amount) for account, asset, amount in normalized],
        )
        return {
            "seq": ledger_seq,
            "event_id": event_id,
            "event_type": event_type,
            "reference": reference,
            "entry_hash": entry_hash,
        }

    def event(self, event_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM ledger_transactions WHERE event_id = ?", (event_id,)
            ).fetchone()
            if not row:
                return None
            entries = self.conn.execute(
                "SELECT account, asset, amount_units FROM ledger_entries WHERE ledger_seq = ? ORDER BY asset, account",
                (row["seq"],),
            ).fetchall()
            return {
                **dict(row),
                "metadata": json.loads(row["metadata_json"]),
                "entries": [dict(entry) for entry in entries],
            }

    def verify(self) -> dict:
        """Verify the hash chain, immutability envelope, and per-asset balances."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM ledger_transactions ORDER BY seq"
            ).fetchall()
            expected_previous = GENESIS_HASH
            checked_entries = 0
            for row in rows:
                entries_rows = self.conn.execute(
                    "SELECT account, asset, amount_units FROM ledger_entries WHERE ledger_seq = ? ORDER BY asset, account, amount_units",
                    (row["seq"],),
                ).fetchall()
                if not entries_rows:
                    raise LedgerError(f"ledger event {row['event_id']} has no entries")
                entries = [(str(e["account"]), str(e["asset"]), int(e["amount_units"])) for e in entries_rows]
                totals: dict[str, int] = defaultdict(int)
                accounts_by_asset: dict[str, set[str]] = defaultdict(set)
                for account, asset, amount in entries:
                    if amount == 0:
                        raise LedgerError(f"ledger event {row['event_id']} contains a zero entry")
                    totals[asset] += amount
                    accounts_by_asset[asset].add(account)
                if any(total != 0 for total in totals.values()):
                    raise LedgerError(f"ledger event {row['event_id']} is unbalanced")
                if any(len(accounts) < 2 for accounts in accounts_by_asset.values()):
                    raise LedgerError(f"ledger event {row['event_id']} does not transfer each asset between accounts")
                if str(row["previous_hash"]) != expected_previous:
                    raise LedgerError(f"ledger hash chain is broken at {row['event_id']}")
                metadata = json.loads(row["metadata_json"])
                payload = {
                    "event_id": str(row["event_id"]),
                    "event_type": str(row["event_type"]),
                    "reference": str(row["reference"]),
                    "created_ns": int(row["created_ns"]),
                    "metadata": metadata,
                    "previous_hash": expected_previous,
                    "entries": entries,
                }
                actual_hash = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
                if actual_hash != str(row["entry_hash"]):
                    raise LedgerError(f"ledger hash mismatch at {row['event_id']}")
                expected_previous = actual_hash
                checked_entries += len(entries)

            orphan = self.conn.execute(
                "SELECT COUNT(*) FROM ledger_entries e LEFT JOIN ledger_transactions t ON t.seq = e.ledger_seq WHERE t.seq IS NULL"
            ).fetchone()[0]
            if int(orphan):
                raise LedgerError("orphan ledger entries detected")
            balances: dict[tuple[str, str], int] = defaultdict(int)
            for entry in self.conn.execute("SELECT account, asset, amount_units FROM ledger_entries"):
                balances[(str(entry["account"]), str(entry["asset"]))] += int(entry["amount_units"])
            for (account, _asset), amount in balances.items():
                if abs(amount) > MAX_INT64:
                    raise LedgerError(f"account balance exceeds SQLite integer range: {account}")
                if account.startswith("user:") and account.endswith((":available", ":reserved")) and amount < 0:
                    raise LedgerError(f"negative user account balance detected: {account}")
            return {"transactions": len(rows), "entries": checked_entries, "head_hash": expected_previous}

    def close(self) -> None:
        with self._lock:
            self.conn.close()
