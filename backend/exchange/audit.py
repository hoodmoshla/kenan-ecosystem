"""Hash-linked audit helpers shared by the DEMO engine and local funder."""
from __future__ import annotations

import hashlib
import json
import time
import uuid
import sqlite3

GENESIS_HASH = "0" * 64


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def append_demo_audit(
    conn: sqlite3.Connection,
    event_type: str,
    user: str,
    reference: str,
    payload: dict,
) -> dict:
    """Append one DEMO-tagged event inside the caller's SQLite transaction."""
    if not conn.in_transaction:
        raise RuntimeError("audit append requires an active transaction")
    normalized_payload = dict(payload)
    environment = normalized_payload.setdefault("environment", "DEMO")
    if environment != "DEMO":
        raise ValueError("audit records must be tagged DEMO")
    previous = conn.execute(
        "SELECT event_hash FROM exchange_audit ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    previous_hash = str(previous[0]) if previous else GENESIS_HASH
    event_id = uuid.uuid4().hex
    created_ns = time.time_ns()
    hash_payload = {
        "environment": "DEMO",
        "event_id": event_id,
        "event_type": event_type,
        "user": user,
        "reference": reference,
        "created_ns": created_ns,
        "payload": normalized_payload,
        "previous_hash": previous_hash,
    }
    event_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT INTO exchange_audit
           (environment, event_id, event_type, user, reference, created_ns,
            payload_json, previous_hash, event_hash)
           VALUES ('DEMO', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            event_type,
            user,
            reference,
            created_ns,
            canonical_json(normalized_payload),
            previous_hash,
            event_hash,
        ),
    )
    return {"event_id": event_id, "event_hash": event_hash}
