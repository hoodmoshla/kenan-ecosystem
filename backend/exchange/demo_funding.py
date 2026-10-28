"""Offline-only local DEMO funding command; not imported by the HTTP app.

Run from the repository root:
    python3 -m backend.exchange.demo_funding grant --user tester \
      --asset DEMO_BASE --amount-units 100 --grant-id run-20261028-01 \
      --i-understand-demo-only

This issues opaque test-ledger units only. It is not KEN, USD, chain issuance,
real custody, or a deposit/withdrawal mechanism.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from .audit import append_demo_audit
from .ledger import DoubleEntryLedger, LedgerError, MAX_INT64
from .schema import (
    DEMO_ASSETS,
    DEMO_ENVIRONMENT,
    MAX_DEMO_ACCOUNT_ASSET_UNITS,
    MAX_DEMO_TOTAL_ISSUANCE_UNITS,
    demo_user_account,
    ensure_demo_schema,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEMO_DB = ROOT / "data" / "demo-exchange.sqlite3"
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
_GRANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,63}$")


class DemoFundingError(ValueError):
    """A local DEMO funding request failed a fixed safety rule."""


def _parse_units(value: Any) -> int:
    if isinstance(value, bool):
        raise DemoFundingError("amount_units must be a positive integer")
    if isinstance(value, int):
        amount = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        amount = int(value)
    else:
        raise DemoFundingError("amount_units must be an integer; floating point is rejected")
    if amount <= 0 or amount > MAX_INT64:
        raise DemoFundingError("amount_units is outside the positive SQLite integer range")
    return amount


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class LocalDemoFunder:
    """A local operator-only facility, intentionally absent from the HTTP app."""

    def __init__(self, ledger: DoubleEntryLedger):
        if not isinstance(ledger, DoubleEntryLedger):
            raise TypeError("LocalDemoFunder requires a DoubleEntryLedger")
        self.ledger = ledger
        self.conn = ledger.conn
        ensure_demo_schema(self.conn)

    def grant(self, user: Any, asset: Any, amount_units: Any, grant_id: Any) -> dict:
        if not isinstance(user, str) or not _USER_RE.fullmatch(user):
            raise DemoFundingError("user must be a valid local DEMO identifier")
        if not isinstance(asset, str) or asset not in DEMO_ASSETS:
            raise DemoFundingError("asset must be one of the allowlisted DEMO assets")
        amount = _parse_units(amount_units)
        if not isinstance(grant_id, str) or not _GRANT_RE.fullmatch(grant_id):
            raise DemoFundingError("grant_id must contain 8-64 safe characters")
        if amount > MAX_DEMO_ACCOUNT_ASSET_UNITS:
            raise DemoFundingError(
                f"grant exceeds the fixed per-account/per-asset cap of {MAX_DEMO_ACCOUNT_ASSET_UNITS} units"
            )

        request = {
            "environment": DEMO_ENVIRONMENT,
            "operation": "local_demo_funding_grant",
            "grant_id": grant_id,
            "user": user,
            "asset": asset,
            "amount_units": amount,
        }
        request_hash = hashlib.sha256(_canonical(request).encode("utf-8")).hexdigest()
        failure: DemoFundingError | None = None
        result: dict | None = None
        with self.ledger.transaction():
            prior_id = self.conn.execute(
                "SELECT * FROM exchange_demo_funding WHERE grant_id = ?", (grant_id,)
            ).fetchone()
            if prior_id:
                if str(prior_id["request_hash"]) == request_hash:
                    append_demo_audit(
                        self.conn,
                        "demo_funding_replayed",
                        user,
                        f"demo-funding:{grant_id}",
                        {"grant_id": grant_id, "request_hash": request_hash},
                    )
                    result = self._result(prior_id, replay=True)
                else:
                    append_demo_audit(
                        self.conn,
                        "demo_funding_rejected",
                        user,
                        f"demo-funding:{grant_id}",
                        {"grant_id": grant_id, "reason": "grant_id_payload_conflict"},
                    )
                    failure = DemoFundingError("grant_id was already used with a different payload")
            else:
                prior_account = self.conn.execute(
                    "SELECT grant_id FROM exchange_demo_funding WHERE user = ? AND asset = ?",
                    (user, asset),
                ).fetchone()
                if prior_account:
                    append_demo_audit(
                        self.conn,
                        "demo_funding_rejected",
                        user,
                        f"demo-funding:{grant_id}",
                        {"grant_id": grant_id, "asset": asset, "reason": "account_asset_already_funded"},
                    )
                    failure = DemoFundingError("this DEMO account/asset has already received its one grant")
                else:
                    total = int(self.conn.execute(
                        "SELECT COALESCE(SUM(amount_units), 0) FROM exchange_demo_funding"
                    ).fetchone()[0])
                    if total + amount > MAX_DEMO_TOTAL_ISSUANCE_UNITS:
                        append_demo_audit(
                            self.conn,
                            "demo_funding_rejected",
                            user,
                            f"demo-funding:{grant_id}",
                            {
                                "grant_id": grant_id,
                                "asset": asset,
                                "amount_units": amount,
                                "issued_before_units": total,
                                "reason": "global_demo_issuance_cap",
                            },
                        )
                        failure = DemoFundingError(
                            f"grant would exceed the fixed global DEMO issuance cap of {MAX_DEMO_TOTAL_ISSUANCE_UNITS} raw units"
                        )
                    else:
                        event_id = f"demo-funding:{grant_id}"
                        metadata = {
                            "environment": DEMO_ENVIRONMENT,
                            "demo_only": True,
                            "grant_id": grant_id,
                            "user": user,
                            "asset": asset,
                            "amount_units": amount,
                            "request_hash": request_hash,
                            "account_asset_cap_units": MAX_DEMO_ACCOUNT_ASSET_UNITS,
                            "global_raw_units_cap": MAX_DEMO_TOTAL_ISSUANCE_UNITS,
                            "unit_note": "opaque DEMO ledger units; not a monetary value or on-chain KEN",
                        }
                        self.ledger.post(
                            event_id,
                            "demo_funding_grant",
                            event_id,
                            [
                                (f"external:DEMO_CLEARING:{asset}", asset, -amount),
                                (demo_user_account(user, asset, "available"), asset, amount),
                            ],
                            metadata,
                        )
                        created_ns = time.time_ns()
                        self.conn.execute(
                            """INSERT INTO exchange_demo_funding
                               (grant_id, environment, user, asset, amount_units,
                                request_hash, ledger_event_id, created_ns)
                               VALUES (?, 'DEMO', ?, ?, ?, ?, ?, ?)""",
                            (grant_id, user, asset, amount, request_hash, event_id, created_ns),
                        )
                        append_demo_audit(
                            self.conn,
                            "demo_funding_grant",
                            user,
                            event_id,
                            {
                                "grant_id": grant_id,
                                "asset": asset,
                                "amount_units": amount,
                                "request_hash": request_hash,
                                "account_asset_cap_units": MAX_DEMO_ACCOUNT_ASSET_UNITS,
                                "global_raw_units_cap": MAX_DEMO_TOTAL_ISSUANCE_UNITS,
                                "unit_note": "opaque DEMO units; not a monetary value or on-chain KEN",
                            },
                        )
                        self.ledger.verify()
                        result = {
                            "environment": DEMO_ENVIRONMENT,
                            "demo_only": True,
                            "grant_id": grant_id,
                            "user": user,
                            "asset": asset,
                            "amount_units": amount,
                            "idempotent_replay": False,
                            "total_issued_after_units": total + amount,
                            "account_asset_cap_units": MAX_DEMO_ACCOUNT_ASSET_UNITS,
                            "global_raw_units_cap": MAX_DEMO_TOTAL_ISSUANCE_UNITS,
                        }
        if failure:
            raise failure
        if result is None:
            raise LedgerError("local DEMO funder completed without a result")
        return result

    @staticmethod
    def _result(row, *, replay: bool) -> dict:
        total = int(row["amount_units"])
        return {
            "environment": DEMO_ENVIRONMENT,
            "demo_only": True,
            "grant_id": str(row["grant_id"]),
            "user": str(row["user"]),
            "asset": str(row["asset"]),
            "amount_units": total,
            "idempotent_replay": replay,
            "account_asset_cap_units": MAX_DEMO_ACCOUNT_ASSET_UNITS,
            "global_raw_units_cap": MAX_DEMO_TOTAL_ISSUANCE_UNITS,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline local DEMO-only funding; no real KEN or HTTP route")
    subparsers = parser.add_subparsers(dest="command", required=True)
    grant_parser = subparsers.add_parser("grant", help="issue one capped DEMO grant to a local test account")
    grant_parser.add_argument("--user", required=True)
    grant_parser.add_argument("--asset", required=True, choices=DEMO_ASSETS)
    grant_parser.add_argument("--amount-units", required=True)
    grant_parser.add_argument("--grant-id", required=True)
    grant_parser.add_argument("--i-understand-demo-only", action="store_true", required=True)
    args = parser.parse_args(argv)
    if args.command != "grant":
        parser.error("unsupported command")

    ledger = DoubleEntryLedger(str(DEFAULT_DEMO_DB))
    try:
        result = LocalDemoFunder(ledger).grant(args.user, args.asset, args.amount_units, args.grant_id)
        print(_canonical(result))
        return 0
    except DemoFundingError as error:
        print(f"DEMO funding rejected: {error}", file=sys.stderr)
        return 2
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
