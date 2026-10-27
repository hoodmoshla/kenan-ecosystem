"""Closed test-only exchange engine backed by the double-entry ledger.

The engine never invents deposits, balances, reference prices, or liquidity.
Amounts are integer ledger units; a limit price is an exact rational number of
quote units per base unit. No on-chain decimals or fee policy are selected here.
"""
from __future__ import annotations

from fractions import Fraction
import hashlib
import json
import re
import time
import uuid
from collections import defaultdict
from math import gcd
from typing import Any

from .ledger import DoubleEntryLedger, LedgerError, MAX_INT64

_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
_ASSET_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")
_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_ACTIVE = ("open", "partially_filled")


class ExchangeError(ValueError):
    """Invalid exchange request or state."""


class InsufficientFunds(ExchangeError):
    """The user's available integer-unit balance cannot cover a reservation."""


class IdempotencyConflict(ExchangeError):
    """An idempotency key was reused with a different request payload."""


class OrderNotFound(ExchangeError):
    """The requested order does not exist."""


class Engine:
    """Persistent order book and settlement engine for local test use only."""

    def __init__(self, db_path: str = ":memory:", allow_test_funding: bool = False):
        self.ledger = DoubleEntryLedger(db_path)
        self.conn = self.ledger.conn
        self._lock = self.ledger._lock
        # This switch is intended only for unit-test fixtures, never HTTP config.
        self.allow_test_funding = bool(allow_test_funding)
        self._create_schema()
        self.reconcile()

    def _create_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS exchange_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    base_asset TEXT NOT NULL,
                    quote_asset TEXT NOT NULL,
                    side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
                    kind TEXT NOT NULL CHECK(kind IN ('limit', 'market')),
                    qty_units INTEGER NOT NULL CHECK(qty_units > 0),
                    limit_price_num INTEGER,
                    limit_price_den INTEGER,
                    quote_budget_units INTEGER,
                    filled_units INTEGER NOT NULL DEFAULT 0 CHECK(filled_units >= 0),
                    reserved_units INTEGER NOT NULL CHECK(reserved_units >= 0),
                    status TEXT NOT NULL CHECK(status IN ('open', 'partially_filled', 'filled', 'cancelled')),
                    cancel_reason TEXT,
                    created_ns INTEGER NOT NULL,
                    updated_ns INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS exchange_orders_book
                    ON exchange_orders(pair, side, status, id);
                CREATE INDEX IF NOT EXISTS exchange_orders_user
                    ON exchange_orders(user, id);
                CREATE TABLE IF NOT EXISTS exchange_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair TEXT NOT NULL,
                    base_asset TEXT NOT NULL,
                    quote_asset TEXT NOT NULL,
                    qty_units INTEGER NOT NULL CHECK(qty_units > 0),
                    quote_units INTEGER NOT NULL CHECK(quote_units > 0),
                    price_num INTEGER NOT NULL CHECK(price_num > 0),
                    price_den INTEGER NOT NULL CHECK(price_den > 0),
                    buyer_user TEXT NOT NULL,
                    seller_user TEXT NOT NULL,
                    buy_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
                    sell_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
                    maker_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
                    taker_order_id INTEGER NOT NULL REFERENCES exchange_orders(id),
                    created_ns INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS exchange_trades_pair ON exchange_trades(pair, id);
                CREATE TABLE IF NOT EXISTS exchange_idempotency (
                    user TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_ns INTEGER NOT NULL,
                    PRIMARY KEY(user, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS exchange_audit (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    user TEXT NOT NULL,
                    reference TEXT NOT NULL,
                    created_ns INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS exchange_audit_no_update
                    BEFORE UPDATE ON exchange_audit BEGIN
                        SELECT RAISE(ABORT, 'exchange audit is immutable');
                    END;
                CREATE TRIGGER IF NOT EXISTS exchange_audit_no_delete
                    BEFORE DELETE ON exchange_audit BEGIN
                        SELECT RAISE(ABORT, 'exchange audit is immutable');
                    END;
                """
            )

    @staticmethod
    def _int_units(value: Any, field: str, *, positive: bool = True) -> int:
        if isinstance(value, bool):
            raise ExchangeError(f"{field} must be an integer unit amount")
        if isinstance(value, int):
            result = value
        elif isinstance(value, str) and value.isascii() and value.isdigit():
            result = int(value)
        else:
            raise ExchangeError(f"{field} must be an integer unit amount; floats are rejected")
        if result > MAX_INT64 or result < 0 or (positive and result == 0):
            qualifier = "positive " if positive else "non-negative "
            raise ExchangeError(f"{field} must be a {qualifier}64-bit integer")
        return result

    @staticmethod
    def _user(user: Any) -> str:
        if not isinstance(user, str) or not _USER_RE.fullmatch(user):
            raise ExchangeError("user must be a valid local test identifier")
        return user

    @staticmethod
    def _key(key: Any) -> str:
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key):
            raise ExchangeError("Idempotency-Key is required (8-128 safe characters)")
        return key

    @staticmethod
    def _pair(pair: Any) -> tuple[str, str, str]:
        if not isinstance(pair, str) or pair.count("/") != 1:
            raise ExchangeError("pair must use BASE/QUOTE symbols")
        base, quote = pair.split("/")
        if not _ASSET_RE.fullmatch(base) or not _ASSET_RE.fullmatch(quote) or base == quote:
            raise ExchangeError("pair symbols must be distinct uppercase identifiers")
        return base + "/" + quote, base, quote

    @staticmethod
    def _ceil_div(numerator: int, denominator: int) -> int:
        return (numerator + denominator - 1) // denominator

    @staticmethod
    def _account(user: str, asset: str, bucket: str) -> str:
        return f"user:{user}:{asset}:{bucket}"

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _hash(value: Any) -> str:
        return hashlib.sha256(Engine._canonical(value).encode("utf-8")).hexdigest()

    @classmethod
    def _price_pair(cls, numerator: Any, denominator: Any) -> tuple[int, int]:
        num = cls._int_units(numerator, "price_num")
        den = cls._int_units(denominator, "price_den")
        common = gcd(num, den)
        return num // common, den // common

    def _append_audit(self, event_type: str, user: str, reference: str, payload: dict) -> dict:
        if not self.conn.in_transaction:
            raise LedgerError("audit append requires an active transaction")
        previous = self.conn.execute(
            "SELECT event_hash FROM exchange_audit ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(previous[0]) if previous else "0" * 64
        event_id = uuid.uuid4().hex
        created_ns = time.time_ns()
        payload_json = self._canonical(payload)
        hash_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "user": user,
            "reference": reference,
            "created_ns": created_ns,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        event_hash = self._hash(hash_payload)
        self.conn.execute(
            """INSERT INTO exchange_audit
               (event_id, event_type, user, reference, created_ns, payload_json, previous_hash, event_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, event_type, user, reference, created_ns, payload_json, previous_hash, event_hash),
        )
        return {"event_id": event_id, "event_hash": event_hash}

    def record_rejected_event(self, event_type: str, *, user: str = "", reference: str = "http", payload: dict | None = None) -> None:
        """Persist a rejected/disabled mutating request without changing balances."""
        with self.ledger.transaction():
            self._append_audit(event_type, user or "anonymous", reference, payload or {})

    def _idempotent_replay(self, user: str, key: str, request_hash: str) -> dict | None:
        row = self.conn.execute(
            "SELECT request_hash, response_json FROM exchange_idempotency WHERE user = ? AND idempotency_key = ?",
            (user, key),
        ).fetchone()
        if not row:
            return None
        if str(row["request_hash"]) != request_hash:
            raise IdempotencyConflict("Idempotency-Key was already used with a different request")
        response = json.loads(row["response_json"])
        self._append_audit("idempotency_replayed", user, key, {"request_hash": request_hash})
        response["idempotent_replay"] = True
        return response

    def _remember_idempotency(self, user: str, key: str, request_hash: str, response: dict) -> None:
        self.conn.execute(
            "INSERT INTO exchange_idempotency(user, idempotency_key, request_hash, response_json, created_ns) VALUES (?, ?, ?, ?, ?)",
            (user, key, request_hash, self._canonical(response), time.time_ns()),
        )

    def issue_test_fixture(self, user: str, asset: str, amount_units: Any, idempotency_key: str) -> dict:
        """Internal unit-test fixture only; intentionally has no HTTP route."""
        if not self.allow_test_funding:
            raise ExchangeError("test fixture funding is disabled")
        user = self._user(user)
        if not isinstance(asset, str) or not _ASSET_RE.fullmatch(asset):
            raise ExchangeError("asset must be an uppercase identifier")
        amount = self._int_units(amount_units, "amount_units")
        key = self._key(idempotency_key)
        payload = {"operation": "test_fixture_credit", "user": user, "asset": asset, "amount_units": amount}
        request_hash = self._hash(payload)
        with self.ledger.transaction():
            replay = self._idempotent_replay(user, key, request_hash)
            if replay is not None:
                return replay
            event_hash = self._hash(["fixture", user, key])[:40]
            self.ledger.post(
                f"test-fixture:{event_hash}",
                "test_fixture_credit",
                f"test-fixture:{user}:{asset}",
                [
                    (f"external:test-fixture:{asset}", asset, -amount),
                    (self._account(user, asset, "available"), asset, amount),
                ],
                {"test_fixture_only": True, "idempotency_key_hash": self._hash(key)},
            )
            response = {"user": user, "asset": asset, "amount_units": amount, "test_fixture_only": True}
            self._append_audit("test_fixture_credit", user, f"fixture:{asset}", response)
            self._remember_idempotency(user, key, request_hash, response)
            self.reconcile()
            return response

    def place(
        self,
        user: str,
        side: str,
        pair: str,
        kind: str = "limit",
        qty_units: Any = None,
        price_num: Any = None,
        price_den: Any = None,
        quote_budget_units: Any = None,
        idempotency_key: str | None = None,
    ) -> dict:
        user = self._user(user)
        if side not in ("buy", "sell"):
            raise ExchangeError("side must be buy or sell")
        if kind not in ("limit", "market"):
            raise ExchangeError("kind must be limit or market")
        pair, base, quote = self._pair(pair)
        qty = self._int_units(qty_units, "qty_units")
        key = self._key(idempotency_key)

        if kind == "limit":
            if price_num is None or price_den is None or quote_budget_units is not None:
                raise ExchangeError("limit orders require price_num/price_den and no quote_budget_units")
            num, den = self._price_pair(price_num, price_den)
            reserve_asset = quote if side == "buy" else base
            reserve = self._ceil_div(num * qty, den) if side == "buy" else qty
            if reserve > MAX_INT64:
                raise ExchangeError("required reservation exceeds SQLite integer range")
            budget = None
        else:
            if price_num is not None or price_den is not None:
                raise ExchangeError("market orders must not supply a price")
            num = den = None
            if side == "buy":
                if quote_budget_units is None:
                    raise ExchangeError("market buy requires quote_budget_units to cap spending")
                budget = self._int_units(quote_budget_units, "quote_budget_units")
                reserve_asset, reserve = quote, budget
            else:
                if quote_budget_units is not None:
                    raise ExchangeError("market sell does not accept quote_budget_units")
                budget = None
                reserve_asset, reserve = base, qty

        payload = {
            "operation": "place_order", "user": user, "side": side, "pair": pair,
            "kind": kind, "qty_units": qty, "price_num": num, "price_den": den,
            "quote_budget_units": budget,
        }
        request_hash = self._hash(payload)
        with self.ledger.transaction():
            replay = self._idempotent_replay(user, key, request_hash)
            if replay is not None:
                return replay
            available_account = self._account(user, reserve_asset, "available")
            if self.ledger.balance(available_account, reserve_asset) < reserve:
                raise InsufficientFunds(f"insufficient available {reserve_asset} ledger units")
            now = time.time_ns()
            cursor = self.conn.execute(
                """INSERT INTO exchange_orders
                   (user, pair, base_asset, quote_asset, side, kind, qty_units,
                    limit_price_num, limit_price_den, quote_budget_units,
                    filled_units, reserved_units, status, cancel_reason, created_ns, updated_ns)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'open', NULL, ?, ?)""",
                (user, pair, base, quote, side, kind, qty, num, den, budget, reserve, now, now),
            )
            order_id = int(cursor.lastrowid)
            self.ledger.post(
                f"order-reserve:{order_id}",
                "order_reservation",
                f"order:{order_id}:reserve",
                [
                    (available_account, reserve_asset, -reserve),
                    (self._account(user, reserve_asset, "reserved"), reserve_asset, reserve),
                ],
                {"order_id": order_id, "side": side, "kind": kind},
            )
            self._append_audit("order_accepted", user, f"order:{order_id}", payload)
            trade_ids = self._match(order_id)
            order = self._get_order(order_id)
            if order["kind"] == "market":
                order = self._finish_market_order(order)
            response = {
                "order": self._order_view(order),
                "trades": [self._trade_view(self._get_trade(trade_id)) for trade_id in trade_ids],
                "fee_policy": "unconfigured_no_fee_charged",
                "test_only": True,
                "idempotent_replay": False,
            }
            self._remember_idempotency(user, key, request_hash, response)
            self.reconcile()
            return response

    def _get_order(self, order_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM exchange_orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise OrderNotFound("order not found")
        return dict(row)

    def _get_trade(self, trade_id: int) -> dict:
        row = self.conn.execute("SELECT * FROM exchange_trades WHERE id = ?", (trade_id,)).fetchone()
        if not row:
            raise ExchangeError("trade not found")
        return dict(row)

    @staticmethod
    def _order_view(order: dict) -> dict:
        return {
            "id": int(order["id"]),
            "user": order["user"],
            "pair": order["pair"],
            "side": order["side"],
            "kind": order["kind"],
            "qty_units": int(order["qty_units"]),
            "filled_units": int(order["filled_units"]),
            "remaining_units": int(order["qty_units"]) - int(order["filled_units"]),
            "reserved_units": int(order["reserved_units"]),
            "status": order["status"],
            "cancel_reason": order["cancel_reason"],
            "limit_price": ({"num": int(order["limit_price_num"]), "den": int(order["limit_price_den"])}
                             if order["limit_price_num"] is not None else None),
            "quote_budget_units": (int(order["quote_budget_units"])
                                    if order["quote_budget_units"] is not None else None),
        }

    @staticmethod
    def _trade_view(trade: dict) -> dict:
        return {
            "id": int(trade["id"]),
            "pair": trade["pair"],
            "qty_units": int(trade["qty_units"]),
            "quote_units": int(trade["quote_units"]),
            "price": {"num": int(trade["price_num"]), "den": int(trade["price_den"])},
            "buyer": trade["buyer_user"],
            "seller": trade["seller_user"],
            "buy_order_id": int(trade["buy_order_id"]),
            "sell_order_id": int(trade["sell_order_id"]),
            "maker_order_id": int(trade["maker_order_id"]),
            "taker_order_id": int(trade["taker_order_id"]),
        }

    def _match(self, incoming_order_id: int) -> list[int]:
        trade_ids: list[int] = []
        while True:
            incoming = self._get_order(incoming_order_id)
            remaining = int(incoming["qty_units"]) - int(incoming["filled_units"])
            if remaining <= 0:
                break
            opposite = "sell" if incoming["side"] == "buy" else "buy"
            rows = self.conn.execute(
                """SELECT * FROM exchange_orders
                   WHERE pair = ? AND side = ? AND kind = 'limit'
                     AND status IN ('open', 'partially_filled')
                     AND filled_units < qty_units
                   ORDER BY id""",
                (incoming["pair"], opposite),
            ).fetchall()
            makers = [dict(row) for row in rows if row["user"] != incoming["user"]]
            if incoming["side"] == "buy":
                makers.sort(key=lambda order: (Fraction(int(order["limit_price_num"]), int(order["limit_price_den"])), int(order["id"])))
            else:
                makers.sort(key=lambda order: (-Fraction(int(order["limit_price_num"]), int(order["limit_price_den"])), int(order["id"])))
            chosen = None
            fill_qty = 0
            quote_cost = 0
            for maker in makers:
                if incoming["kind"] == "limit":
                    incoming_price = Fraction(int(incoming["limit_price_num"]), int(incoming["limit_price_den"]))
                    maker_price = Fraction(int(maker["limit_price_num"]), int(maker["limit_price_den"]))
                    if incoming["side"] == "buy" and incoming_price < maker_price:
                        break
                    if incoming["side"] == "sell" and incoming_price > maker_price:
                        break
                maker_remaining = int(maker["qty_units"]) - int(maker["filled_units"])
                cap = min(remaining, maker_remaining)
                price_num = int(maker["limit_price_num"])
                price_den = int(maker["limit_price_den"])
                if incoming["side"] == "buy" and incoming["kind"] == "market":
                    affordable = int(incoming["reserved_units"]) * price_den // price_num
                    cap = min(cap, affordable)
                step = price_den // gcd(price_num, price_den)
                candidate_qty = (cap // step) * step
                if candidate_qty <= 0:
                    continue
                quote_cost = candidate_qty * price_num // price_den
                if quote_cost <= 0 or quote_cost > MAX_INT64:
                    continue
                fill_qty = candidate_qty
                chosen = maker
                break
            if chosen is None:
                break
            trade_id = self._settle_fill(incoming_order_id, int(chosen["id"]), fill_qty, quote_cost)
            trade_ids.append(trade_id)
        return trade_ids

    def _settle_fill(self, incoming_id: int, maker_id: int, qty: int, quote_cost: int) -> int:
        incoming = self._get_order(incoming_id)
        maker = self._get_order(maker_id)
        buyer = incoming if incoming["side"] == "buy" else maker
        seller = incoming if incoming["side"] == "sell" else maker
        if buyer["user"] == seller["user"]:
            raise LedgerError("self-trades must not be settled")
        if buyer["kind"] == "market":
            quote_debit = quote_cost
            improvement = 0
            buyer_new_reserved = int(buyer["reserved_units"]) - quote_cost
        else:
            old_reserved = int(buyer["reserved_units"])
            new_buyer_filled = int(buyer["filled_units"]) + qty
            buyer_remaining = int(buyer["qty_units"]) - new_buyer_filled
            target_reserved = self._ceil_div(
                int(buyer["limit_price_num"]) * buyer_remaining,
                int(buyer["limit_price_den"]),
            )
            release = old_reserved - target_reserved - quote_cost
            if release < 0:
                raise LedgerError("buyer reservation is insufficient for exact settlement")
            quote_debit = quote_cost + release
            improvement = release
            buyer_new_reserved = target_reserved

        seller_new_reserved = int(seller["reserved_units"]) - qty
        if seller_new_reserved < 0 or buyer_new_reserved < 0:
            raise LedgerError("order reservation underflow")
        now = time.time_ns()
        trade_cursor = self.conn.execute(
            """INSERT INTO exchange_trades
               (pair, base_asset, quote_asset, qty_units, quote_units, price_num, price_den,
                buyer_user, seller_user, buy_order_id, sell_order_id, maker_order_id, taker_order_id, created_ns)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                incoming["pair"], incoming["base_asset"], incoming["quote_asset"], qty, quote_cost,
                int(maker["limit_price_num"]), int(maker["limit_price_den"]),
                buyer["user"], seller["user"], int(buyer["id"]), int(seller["id"]), maker_id, incoming_id, now,
            ),
        )
        trade_id = int(trade_cursor.lastrowid)
        entries = [
            (self._account(seller["user"], seller["base_asset"], "reserved"), seller["base_asset"], -qty),
            (self._account(buyer["user"], buyer["base_asset"], "available"), buyer["base_asset"], qty),
            (self._account(buyer["user"], buyer["quote_asset"], "reserved"), buyer["quote_asset"], -quote_debit),
            (self._account(seller["user"], seller["quote_asset"], "available"), seller["quote_asset"], quote_cost),
        ]
        if improvement:
            entries.append((self._account(buyer["user"], buyer["quote_asset"], "available"), buyer["quote_asset"], improvement))
        self.ledger.post(
            f"exchange-trade:{trade_id}",
            "trade_settlement",
            f"trade:{trade_id}",
            entries,
            {"trade_id": trade_id, "buy_order_id": int(buyer["id"]), "sell_order_id": int(seller["id"]), "fee_policy": "unconfigured"},
        )
        incoming_new_filled = int(incoming["filled_units"]) + qty
        maker_new_filled = int(maker["filled_units"]) + qty
        self.conn.execute(
            "UPDATE exchange_orders SET filled_units = ?, reserved_units = ?, status = ?, updated_ns = ? WHERE id = ?",
            (incoming_new_filled, self._reserved_for_order(incoming, buyer, seller, buyer_new_reserved, seller_new_reserved),
             self._next_status(incoming_new_filled, int(incoming["qty_units"])), now, incoming_id),
        )
        self.conn.execute(
            "UPDATE exchange_orders SET filled_units = ?, reserved_units = ?, status = ?, updated_ns = ? WHERE id = ?",
            (maker_new_filled, self._reserved_for_order(maker, buyer, seller, buyer_new_reserved, seller_new_reserved),
             self._next_status(maker_new_filled, int(maker["qty_units"])), now, maker_id),
        )
        self._append_audit(
            "trade_settled", buyer["user"], f"trade:{trade_id}",
            {"trade_id": trade_id, "buyer": buyer["user"], "seller": seller["user"],
             "qty_units": qty, "quote_units": quote_cost, "maker_order_id": maker_id, "taker_order_id": incoming_id},
        )
        return trade_id

    @staticmethod
    def _reserved_for_order(order: dict, buyer: dict, seller: dict, buyer_reserved: int, seller_reserved: int) -> int:
        if int(order["id"]) == int(buyer["id"]):
            return buyer_reserved
        if int(order["id"]) == int(seller["id"]):
            return seller_reserved
        raise LedgerError("fill references an unrelated order")

    @staticmethod
    def _next_status(filled: int, qty: int) -> str:
        if filled == qty:
            return "filled"
        if filled > 0:
            return "partially_filled"
        return "open"

    def _finish_market_order(self, order: dict) -> dict:
        order = self._get_order(int(order["id"]))
        remaining_hold = int(order["reserved_units"])
        if remaining_hold:
            asset = order["quote_asset"] if order["side"] == "buy" else order["base_asset"]
            self.ledger.post(
                f"market-remainder-release:{order['id']}",
                "market_remainder_release",
                f"order:{order['id']}:market-remainder-release",
                [
                    (self._account(order["user"], asset, "reserved"), asset, -remaining_hold),
                    (self._account(order["user"], asset, "available"), asset, remaining_hold),
                ],
                {"order_id": int(order["id"]), "unspent_or_unfilled_units": remaining_hold},
            )
        status = "filled" if int(order["filled_units"]) == int(order["qty_units"]) else "cancelled"
        reason = None if status == "filled" else "market_remainder_not_resting"
        self.conn.execute(
            "UPDATE exchange_orders SET reserved_units = 0, status = ?, cancel_reason = ?, updated_ns = ? WHERE id = ?",
            (status, reason, time.time_ns(), int(order["id"])),
        )
        if reason:
            self._append_audit("market_remainder_cancelled", order["user"], f"order:{order['id']}",
                               {"remaining_units": int(order["qty_units"]) - int(order["filled_units"]), "reason": reason})
        return self._get_order(int(order["id"]))

    def cancel(self, order_id: Any, user: str, idempotency_key: str | None = None) -> dict:
        user = self._user(user)
        oid = self._int_units(order_id, "order_id")
        key = self._key(idempotency_key)
        payload = {"operation": "cancel_order", "user": user, "order_id": oid}
        request_hash = self._hash(payload)
        with self.ledger.transaction():
            replay = self._idempotent_replay(user, key, request_hash)
            if replay is not None:
                return replay
            order = self._get_order(oid)
            if order["user"] != user:
                raise PermissionError("order does not belong to this local test user")
            if order["status"] in _ACTIVE:
                held = int(order["reserved_units"])
                if held:
                    asset = order["quote_asset"] if order["side"] == "buy" else order["base_asset"]
                    self.ledger.post(
                        f"order-cancel-release:{oid}",
                        "order_cancel_release",
                        f"order:{oid}:cancel-release",
                        [
                            (self._account(user, asset, "reserved"), asset, -held),
                            (self._account(user, asset, "available"), asset, held),
                        ],
                        {"order_id": oid},
                    )
                self.conn.execute(
                    "UPDATE exchange_orders SET reserved_units = 0, status = 'cancelled', cancel_reason = 'user_cancelled', updated_ns = ? WHERE id = ?",
                    (time.time_ns(), oid),
                )
                self._append_audit("order_cancelled", user, f"order:{oid}", {"released_units": held})
            else:
                self._append_audit("order_cancel_noop", user, f"order:{oid}", {"status": order["status"]})
            result = {"order": self._order_view(self._get_order(oid)), "test_only": True, "idempotent_replay": False}
            self._remember_idempotency(user, key, request_hash, result)
            self.reconcile()
            return result

    def balances_snapshot(self, user: str) -> dict:
        user = self._user(user)
        prefix = f"user:{user}:"
        with self._lock:
            rows = self.conn.execute(
                "SELECT DISTINCT account, asset FROM ledger_entries WHERE substr(account, 1, ?) = ?",
                (len(prefix), prefix),
            ).fetchall()
        assets = set()
        for row in rows:
            parts = str(row["account"])[len(prefix):].split(":")
            if len(parts) == 2 and parts[1] in ("available", "reserved"):
                assets.add(str(row["asset"]))
        result = {}
        for asset in sorted(assets):
            free = self.ledger.balance(self._account(user, asset, "available"), asset)
            reserved = self.ledger.balance(self._account(user, asset, "reserved"), asset)
            if free or reserved:
                result[asset] = {"available_units": free, "reserved_units": reserved}
        return result

    def book(self, pair: str) -> dict:
        pair, _, _ = self._pair(pair)
        with self._lock:
            rows = self.conn.execute(
                """SELECT * FROM exchange_orders WHERE pair = ? AND kind = 'limit'
                   AND status IN ('open', 'partially_filled') AND filled_units < qty_units""",
                (pair,),
            ).fetchall()
            orders = [dict(row) for row in rows]
        asks = [o for o in orders if o["side"] == "sell"]
        bids = [o for o in orders if o["side"] == "buy"]
        asks.sort(key=lambda o: (Fraction(int(o["limit_price_num"]), int(o["limit_price_den"])), int(o["id"])))
        bids.sort(key=lambda o: (-Fraction(int(o["limit_price_num"]), int(o["limit_price_den"])), int(o["id"])))
        def view(order: dict) -> dict:
            return {
                "id": int(order["id"]),
                "price": {"num": int(order["limit_price_num"]), "den": int(order["limit_price_den"])},
                "qty_units": int(order["qty_units"]) - int(order["filled_units"]),
            }
        return {"pair": pair, "asks": [view(o) for o in asks], "bids": [view(o) for o in bids],
                "test_only": True, "price_source": "submitted_limit_orders_only"}

    def orders_snapshot(self, user: str | None = None) -> list[dict]:
        with self._lock:
            if user is None:
                rows = self.conn.execute("SELECT * FROM exchange_orders ORDER BY id").fetchall()
            else:
                user = self._user(user)
                rows = self.conn.execute("SELECT * FROM exchange_orders WHERE user = ? ORDER BY id", (user,)).fetchall()
            return [self._order_view(dict(row)) for row in rows]

    def trades_snapshot(self, user: str | None = None) -> list[dict]:
        with self._lock:
            if user is None:
                rows = self.conn.execute("SELECT * FROM exchange_trades ORDER BY id").fetchall()
            else:
                user = self._user(user)
                rows = self.conn.execute(
                    "SELECT * FROM exchange_trades WHERE buyer_user = ? OR seller_user = ? ORDER BY id",
                    (user, user),
                ).fetchall()
            return [self._trade_view(dict(row)) for row in rows]

    def audit_snapshot(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM exchange_audit ORDER BY seq").fetchall()
            return [dict(row) for row in rows]

    def _verify_audit(self) -> int:
        rows = self.conn.execute("SELECT * FROM exchange_audit ORDER BY seq").fetchall()
        previous = "0" * 64
        for row in rows:
            if str(row["previous_hash"]) != previous:
                raise LedgerError(f"exchange audit hash chain broken at {row['event_id']}")
            payload = json.loads(row["payload_json"])
            obj = {
                "event_id": str(row["event_id"]),
                "event_type": str(row["event_type"]),
                "user": str(row["user"]),
                "reference": str(row["reference"]),
                "created_ns": int(row["created_ns"]),
                "payload": payload,
                "previous_hash": previous,
            }
            actual = self._hash(obj)
            if actual != str(row["event_hash"]):
                raise LedgerError(f"exchange audit hash mismatch at {row['event_id']}")
            previous = actual
        return len(rows)

    def reconcile(self) -> dict:
        """Re-derive reserved balances from active orders and verify all journals."""
        with self._lock:
            ledger_report = self.ledger.verify()
            expected: dict[tuple[str, str], int] = defaultdict(int)
            rows = self.conn.execute("SELECT * FROM exchange_orders ORDER BY id").fetchall()
            order_ids = set()
            orders_by_id = {}
            for row in rows:
                order = dict(row)
                order_id = int(order["id"])
                order_ids.add(order_id)
                orders_by_id[order_id] = order
                qty = int(order["qty_units"])
                filled = int(order["filled_units"])
                held = int(order["reserved_units"])
                if qty <= 0 or filled < 0 or filled > qty:
                    raise LedgerError(f"invalid quantity state in order {order_id}")
                if order["status"] == "filled" and filled != qty:
                    raise LedgerError(f"filled order {order_id} has unfilled quantity")
                if order["status"] in _ACTIVE:
                    if filled >= qty or order["kind"] != "limit":
                        raise LedgerError(f"invalid active order state in order {order_id}")
                    asset = order["quote_asset"] if order["side"] == "buy" else order["base_asset"]
                    remaining = qty - filled
                    required_hold = (self._ceil_div(int(order["limit_price_num"]) * remaining,
                                                    int(order["limit_price_den"]))
                                     if order["side"] == "buy" else remaining)
                    if held != required_hold:
                        raise LedgerError(f"order {order_id} reservation does not match its remaining quantity")
                    expected[(self._account(order["user"], asset, "reserved"), asset)] += held
                    if held <= 0:
                        raise LedgerError(f"active order {order_id} has no reservation")
                elif held != 0:
                    raise LedgerError(f"terminal order {order_id} retains reserved funds")
                if order["kind"] == "market" and order["status"] not in ("filled", "cancelled"):
                    raise LedgerError(f"market order {order_id} was left resting")
                reserve_event = self.conn.execute(
                    "SELECT 1 FROM ledger_transactions WHERE event_id = ?",
                    (f"order-reserve:{order_id}",),
                ).fetchone()
                if not reserve_event:
                    raise LedgerError(f"order {order_id} lacks its reservation journal")

            reserved_rows = self.conn.execute(
                "SELECT DISTINCT account, asset FROM ledger_entries WHERE account LIKE 'user:%:reserved'"
            ).fetchall()
            actual_reserved: dict[tuple[str, str], int] = {}
            for row in reserved_rows:
                account, asset = str(row["account"]), str(row["asset"])
                value = self.ledger.balance(account, asset)
                if value < 0:
                    raise LedgerError(f"negative reserved balance: {account}")
                if value:
                    actual_reserved[(account, asset)] = value
            expected = {key: value for key, value in expected.items() if value}
            if expected != actual_reserved:
                raise LedgerError(f"reserved balance/order mismatch: expected {expected}, actual {actual_reserved}")

            trade_rows = self.conn.execute("SELECT * FROM exchange_trades ORDER BY id").fetchall()
            trade_ids = set()
            filled_by_order: dict[int, int] = defaultdict(int)
            for trade in trade_rows:
                trade_id = int(trade["id"])
                trade_ids.add(trade_id)
                qty = int(trade["qty_units"])
                price_num = int(trade["price_num"])
                price_den = int(trade["price_den"])
                quote_units = int(trade["quote_units"])
                if qty <= 0 or price_num <= 0 or price_den <= 0 or qty * price_num % price_den:
                    raise LedgerError(f"trade {trade_id} is not exactly representable in integer units")
                if qty * price_num // price_den != quote_units:
                    raise LedgerError(f"trade {trade_id} quote amount does not match its price")
                if gcd(price_num, price_den) != 1:
                    raise LedgerError(f"trade {trade_id} price ratio is not canonical")
                buy_id, sell_id = int(trade["buy_order_id"]), int(trade["sell_order_id"])
                buy_order, sell_order = orders_by_id.get(buy_id), orders_by_id.get(sell_id)
                if not buy_order or not sell_order or buy_order["side"] != "buy" or sell_order["side"] != "sell":
                    raise LedgerError(f"trade {trade_id} does not reference valid buy/sell orders")
                if buy_order["pair"] != trade["pair"] or sell_order["pair"] != trade["pair"]:
                    raise LedgerError(f"trade {trade_id} pair differs from its orders")
                if buy_order["user"] != trade["buyer_user"] or sell_order["user"] != trade["seller_user"]:
                    raise LedgerError(f"trade {trade_id} parties differ from its orders")
                if int(trade["maker_order_id"]) not in (buy_id, sell_id) or int(trade["taker_order_id"]) not in (buy_id, sell_id):
                    raise LedgerError(f"trade {trade_id} maker/taker references are invalid")
                filled_by_order[buy_id] += qty
                filled_by_order[sell_id] += qty
            for order_id, order in orders_by_id.items():
                if int(order["filled_units"]) != filled_by_order.get(order_id, 0):
                    raise LedgerError(f"order {order_id} fill amount differs from its trade history")
            settled_refs = self.conn.execute(
                "SELECT reference FROM ledger_transactions WHERE event_type = 'trade_settlement'"
            ).fetchall()
            settled_ids = {int(str(row[0]).split(":", 1)[1]) for row in settled_refs}
            if trade_ids != settled_ids:
                raise LedgerError("trade rows and settlement journals do not reconcile")
            audit_count = self._verify_audit()
            return {
                "ledger_transactions": ledger_report["transactions"],
                "ledger_entries": ledger_report["entries"],
                "orders": len(order_ids),
                "trades": len(trade_ids),
                "audit_events": audit_count,
                "ok": True,
            }

    def counts(self) -> dict[str, int]:
        with self._lock:
            open_count = int(self.conn.execute(
                "SELECT COUNT(*) FROM exchange_orders WHERE status IN ('open', 'partially_filled')"
            ).fetchone()[0])
            order_count = int(self.conn.execute("SELECT COUNT(*) FROM exchange_orders").fetchone()[0])
            trade_count = int(self.conn.execute("SELECT COUNT(*) FROM exchange_trades").fetchone()[0])
            return {"open_orders": open_count, "orders": order_count, "trades": trade_count}

    def close(self) -> None:
        self.ledger.close()
