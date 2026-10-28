"""Versioned schema and hard boundaries for the local DEMO exchange only."""
from __future__ import annotations

import sqlite3

DEMO_ENVIRONMENT = "DEMO"
DEMO_ASSETS = ("DEMO_BASE", "DEMO_QUOTE")
MAX_DEMO_ACCOUNT_ASSET_UNITS = 10_000
MAX_DEMO_TOTAL_ISSUANCE_UNITS = 100_000
MAX_DEMO_FEE_BPS = 1_000
SCHEMA_VERSION = 1


def demo_user_account(user: str, asset: str, bucket: str) -> str:
    if bucket not in ("available", "reserved"):
        raise ValueError("invalid DEMO balance bucket")
    return f"user:DEMO:{user}:{asset}:{bucket}"


DEMO_SCHEMA_SQL = f"""
CREATE TABLE exchange_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL DEFAULT 'DEMO' CHECK(environment = 'DEMO'),
    user TEXT NOT NULL,
    pair TEXT NOT NULL,
    base_asset TEXT NOT NULL CHECK(base_asset IN ('DEMO_BASE', 'DEMO_QUOTE')),
    quote_asset TEXT NOT NULL CHECK(quote_asset IN ('DEMO_BASE', 'DEMO_QUOTE')),
    side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
    kind TEXT NOT NULL CHECK(kind IN ('limit', 'market')),
    qty_units INTEGER NOT NULL CHECK(qty_units > 0),
    limit_price_num INTEGER,
    limit_price_den INTEGER,
    quote_budget_units INTEGER,
    fee_bps INTEGER NOT NULL CHECK(fee_bps BETWEEN 0 AND {MAX_DEMO_FEE_BPS}),
    filled_units INTEGER NOT NULL DEFAULT 0 CHECK(filled_units >= 0),
    reserved_units INTEGER NOT NULL CHECK(reserved_units >= 0),
    status TEXT NOT NULL CHECK(status IN ('open', 'partially_filled', 'filled', 'cancelled')),
    cancel_reason TEXT,
    created_ns INTEGER NOT NULL,
    updated_ns INTEGER NOT NULL
);
CREATE INDEX exchange_orders_book ON exchange_orders(pair, side, status, id);
CREATE INDEX exchange_orders_user ON exchange_orders(user, id);

CREATE TABLE exchange_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL DEFAULT 'DEMO' CHECK(environment = 'DEMO'),
    pair TEXT NOT NULL,
    base_asset TEXT NOT NULL CHECK(base_asset IN ('DEMO_BASE', 'DEMO_QUOTE')),
    quote_asset TEXT NOT NULL CHECK(quote_asset IN ('DEMO_BASE', 'DEMO_QUOTE')),
    qty_units INTEGER NOT NULL CHECK(qty_units > 0),
    quote_units INTEGER NOT NULL CHECK(quote_units > 0),
    price_num INTEGER NOT NULL CHECK(price_num > 0),
    price_den INTEGER NOT NULL CHECK(price_den > 0),
    fee_units INTEGER NOT NULL CHECK(fee_units >= 0),
    fee_asset TEXT NOT NULL CHECK(fee_asset = 'DEMO_QUOTE'),
    fee_bps INTEGER NOT NULL CHECK(fee_bps BETWEEN 0 AND {MAX_DEMO_FEE_BPS}),
    buyer_reserved_debit_units INTEGER NOT NULL CHECK(buyer_reserved_debit_units > 0),
    buyer_release_units INTEGER NOT NULL CHECK(buyer_release_units >= 0),
    buyer_user TEXT NOT NULL,
    seller_user TEXT NOT NULL,
    buy_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
    sell_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
    maker_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
    taker_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
    created_ns INTEGER NOT NULL
);
CREATE INDEX exchange_trades_pair ON exchange_trades(pair, id);
CREATE INDEX exchange_trades_buyer ON exchange_trades(buyer_user, id);
CREATE INDEX exchange_trades_seller ON exchange_trades(seller_user, id);
CREATE TRIGGER exchange_trades_no_update BEFORE UPDATE ON exchange_trades BEGIN
    SELECT RAISE(ABORT, 'DEMO trade history is immutable');
END;
CREATE TRIGGER exchange_trades_no_delete BEFORE DELETE ON exchange_trades BEGIN
    SELECT RAISE(ABORT, 'DEMO trade history is immutable');
END;

CREATE TABLE exchange_idempotency (
    environment TEXT NOT NULL DEFAULT 'DEMO' CHECK(environment = 'DEMO'),
    user TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_ns INTEGER NOT NULL,
    PRIMARY KEY(user, idempotency_key)
);
CREATE TRIGGER exchange_idempotency_no_update BEFORE UPDATE ON exchange_idempotency BEGIN
    SELECT RAISE(ABORT, 'DEMO idempotency records are immutable');
END;
CREATE TRIGGER exchange_idempotency_no_delete BEFORE DELETE ON exchange_idempotency BEGIN
    SELECT RAISE(ABORT, 'DEMO idempotency records are immutable');
END;

CREATE TABLE exchange_audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL DEFAULT 'DEMO' CHECK(environment = 'DEMO'),
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    user TEXT NOT NULL,
    reference TEXT NOT NULL,
    created_ns INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL
);
CREATE TRIGGER exchange_audit_no_update BEFORE UPDATE ON exchange_audit BEGIN
    SELECT RAISE(ABORT, 'DEMO audit is immutable');
END;
CREATE TRIGGER exchange_audit_no_delete BEFORE DELETE ON exchange_audit BEGIN
    SELECT RAISE(ABORT, 'DEMO audit is immutable');
END;

CREATE TABLE exchange_demo_funding (
    grant_id TEXT PRIMARY KEY,
    environment TEXT NOT NULL DEFAULT 'DEMO' CHECK(environment = 'DEMO'),
    user TEXT NOT NULL,
    asset TEXT NOT NULL CHECK(asset IN ('DEMO_BASE', 'DEMO_QUOTE')),
    amount_units INTEGER NOT NULL CHECK(amount_units BETWEEN 1 AND {MAX_DEMO_ACCOUNT_ASSET_UNITS}),
    request_hash TEXT NOT NULL,
    ledger_event_id TEXT NOT NULL UNIQUE REFERENCES ledger_transactions(event_id),
    created_ns INTEGER NOT NULL,
    UNIQUE(user, asset)
);
CREATE TRIGGER exchange_demo_funding_no_update BEFORE UPDATE ON exchange_demo_funding BEGIN
    SELECT RAISE(ABORT, 'DEMO funding grants are immutable');
END;
CREATE TRIGGER exchange_demo_funding_no_delete BEFORE DELETE ON exchange_demo_funding BEGIN
    SELECT RAISE(ABORT, 'DEMO funding grants are immutable');
END;
"""

_REQUIRED_TABLES = {
    "exchange_orders",
    "exchange_trades",
    "exchange_idempotency",
    "exchange_audit",
    "exchange_demo_funding",
}
_LEGACY_TABLES = {
    "exchange_orders",
    "exchange_trades",
    "exchange_idempotency",
    "exchange_audit",
}
_ENVIRONMENT_TABLES = _REQUIRED_TABLES
_REQUIRED_IMMUTABILITY_TRIGGERS = {
    "exchange_trades_no_update",
    "exchange_trades_no_delete",
    "exchange_idempotency_no_update",
    "exchange_idempotency_no_delete",
    "exchange_audit_no_update",
    "exchange_audit_no_delete",
    "exchange_demo_funding_no_update",
    "exchange_demo_funding_no_delete",
}


class DemoSchemaError(RuntimeError):
    """The SQLite file is not a recognized, isolated DEMO exchange database."""


def ensure_demo_schema(conn: sqlite3.Connection) -> None:
    """Create the DEMO schema once, or fail closed on legacy/partial schemas."""
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if version == SCHEMA_VERSION:
        missing = _REQUIRED_TABLES - tables
        if missing:
            raise DemoSchemaError(f"DEMO database is incomplete; missing tables: {sorted(missing)}")
        for table in _ENVIRONMENT_TABLES:
            columns = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if "environment" not in columns:
                raise DemoSchemaError(f"DEMO database table {table} lacks its environment tag")
        triggers = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        missing_triggers = _REQUIRED_IMMUTABILITY_TRIGGERS - triggers
        if missing_triggers:
            raise DemoSchemaError(f"DEMO database is missing immutable-record triggers: {sorted(missing_triggers)}")
        return
    if version != 0:
        raise DemoSchemaError(f"unsupported DEMO database schema version: {version}")
    legacy = _LEGACY_TABLES & tables
    if legacy:
        raise DemoSchemaError(
            "unversioned legacy exchange database refused; preserve it and use the dedicated data/demo-exchange.sqlite3 file"
        )
    try:
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            + DEMO_SCHEMA_SQL
            + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise
