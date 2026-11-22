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
from collections import defaultdict
from math import gcd
from typing import Any

from .audit import append_demo_audit, GENESIS_HASH
from .ledger import DoubleEntryLedger, LedgerError, MAX_INT64
from .schema import (
    DEMO_ASSETS,
    DEMO_ENVIRONMENT,
    MAX_DEMO_ACCOUNT_ASSET_UNITS,
    MAX_DEMO_FEE_BPS,
    MAX_DEMO_TOTAL_ISSUANCE_UNITS,
    demo_user_account,
    ensure_demo_schema,
)

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

    def __init__(self, db_path: str = ":memory:", *, demo_fee_bps: int = 0):
        self.demo_fee_bps = self._fee_bps(demo_fee_bps)
        self.ledger = DoubleEntryLedger(db_path)
        self.conn = self.ledger.conn
        self._lock = self.ledger._lock
        ensure_demo_schema(self.conn)
        self.reconcile()

    @staticmethod
    def _fee_bps(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_DEMO_FEE_BPS:
            raise ExchangeError(f"demo_fee_bps must be an integer in 0..{MAX_DEMO_FEE_BPS}")
        return value

    def _demo_fee_for_quote(self, quote_units: int, fee_bps: int | None = None) -> int:
        rate = self.demo_fee_bps if fee_bps is None else self._fee_bps(fee_bps)
        return quote_units * rate // 10_000 if rate else 0

    def _limit_buy_reserve(self, price_num: int, price_den: int, qty_units: int, fee_bps: int) -> int:
        principal = self._ceil_div(price_num * qty_units, price_den)
        return principal + self._demo_fee_for_quote(principal, fee_bps)

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
            raise ExchangeError("pair must use DEMO_BASE/DEMO_QUOTE symbols")
        base, quote = pair.split("/")
        if (base, quote) != ("DEMO_BASE", "DEMO_QUOTE") or base not in DEMO_ASSETS or quote not in DEMO_ASSETS:
            raise ExchangeError("the only enabled pair is DEMO_BASE/DEMO_QUOTE")
        return base + "/" + quote, base, quote

    @staticmethod
    def _ceil_div(numerator: int, denominator: int) -> int:
        return (numerator + denominator - 1) // denominator

    @staticmethod
    def _account(user: str, asset: str, bucket: str) -> str:
        return demo_user_account(user, asset, bucket)

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
        return append_demo_audit(self.conn, event_type, user, reference, payload)

    def record_rejected_event(self, event_type: str, *, user: str = "", reference: str = "http", payload: dict | None = None) -> None:
        """Persist a rejected/disabled mutating request without changing balances."""
        with self.ledger.transaction():
            self._append_audit(event_type, user or "anonymous", reference, payload or {})

    def _idempotent_replay(self, user: str, key: str, request_hash: str) -> dict | None:
        row = self.conn.execute(
            "SELECT request_hash, response_json FROM exchange_idempotency WHERE environment = 'DEMO' AND user = ? AND idempotency_key = ?",
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
            "INSERT INTO exchange_idempotency(environment, user, idempotency_key, request_hash, response_json, created_ns) VALUES ('DEMO', ?, ?, ?, ?, ?)",
            (user, key, request_hash, self._canonical(response), time.time_ns()),
        )

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
            reserve = self._limit_buy_reserve(num, den, qty, self.demo_fee_bps) if side == "buy" else qty
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
            "environment": DEMO_ENVIRONMENT, "operation": "place_order", "user": user, "side": side, "pair": pair,
            "kind": kind, "qty_units": qty, "price_num": num, "price_den": den,
            "quote_budget_units": budget, "demo_fee_bps_test_only": self.demo_fee_bps,
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
                   (environment, user, pair, base_asset, quote_asset, side, kind, qty_units,
                    limit_price_num, limit_price_den, quote_budget_units,
                    fee_bps, filled_units, reserved_units, status, cancel_reason, created_ns, updated_ns)
                   VALUES ('DEMO', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'open', NULL, ?, ?)""",
                (user, pair, base, quote, side, kind, qty, num, den, budget, self.demo_fee_bps, reserve, now, now),
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
                {"environment": DEMO_ENVIRONMENT, "order_id": order_id, "side": side, "kind": kind},
            )
            self._append_audit("order_accepted", user, f"order:{order_id}", payload)
            trade_ids = self._match(order_id)
            order = self._get_order(order_id)
            if order["kind"] == "market":
                order = self._finish_market_order(order)
            response = {
                "order": self._order_view(order),
                "trades": [self._trade_view(self._get_trade(trade_id)) for trade_id in trade_ids],
                "fee_policy": ("DEMO_test_only_quote_fee" if self.demo_fee_bps else "unconfigured_no_fee_charged"),
                "demo_fee_bps_test_only": self.demo_fee_bps,
                "environment": DEMO_ENVIRONMENT,
                "demo_only": True,
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
            "environment": DEMO_ENVIRONMENT,
            "demo_only": True,
            "test_only": True,
            "id": int(order["id"]),
            "user": order["user"],
            "pair": order["pair"],
            "side": order["side"],
            "kind": order["kind"],
            "qty_units": int(order["qty_units"]),
            "filled_units": int(order["filled_units"]),
            "remaining_units": int(order["qty_units"]) - int(order["filled_units"]),
            "reserved_units": int(order["reserved_units"]),
            "demo_fee_bps_test_only": int(order["fee_bps"]),
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
            "environment": DEMO_ENVIRONMENT,
            "demo_only": True,
            "test_only": True,
            "id": int(trade["id"]),
            "pair": trade["pair"],
            "qty_units": int(trade["qty_units"]),
            "quote_units": int(trade["quote_units"]),
            "fee_units": int(trade["fee_units"]),
            "fee_asset": str(trade["fee_asset"]),
            "demo_fee_bps_test_only": int(trade["fee_bps"]),
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
                step = price_den // gcd(price_num, price_den)
                max_steps = cap // step
                if incoming["side"] == "buy" and incoming["kind"] == "market":
                    low, high = 0, max_steps
                    budget = int(incoming["reserved_units"])
                    fee_bps = int(incoming["fee_bps"])
                    while low < high:
                        middle = (low + high + 1) // 2
                        candidate = middle * step
                        principal = candidate * price_num // price_den
                        if principal + self._demo_fee_for_quote(principal, fee_bps) <= budget:
                            low = middle
                        else:
                            high = middle - 1
                    max_steps = low
                candidate_qty = max_steps * step
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
        fee_bps = int(buyer["fee_bps"])
        fee_units = self._demo_fee_for_quote(quote_cost, fee_bps)
        if buyer["kind"] == "market":
            quote_debit = quote_cost + fee_units
            improvement = 0
            buyer_new_reserved = int(buyer["reserved_units"]) - quote_debit
        else:
            old_reserved = int(buyer["reserved_units"])
            new_buyer_filled = int(buyer["filled_units"]) + qty
            buyer_remaining = int(buyer["qty_units"]) - new_buyer_filled
            target_reserved = self._limit_buy_reserve(
                int(buyer["limit_price_num"]), int(buyer["limit_price_den"]), buyer_remaining, fee_bps
            )
            release = old_reserved - target_reserved - quote_cost - fee_units
            if release < 0:
                raise LedgerError("buyer reservation is insufficient for exact settlement")
            quote_debit = quote_cost + fee_units + release
            improvement = release
            buyer_new_reserved = target_reserved

        seller_new_reserved = int(seller["reserved_units"]) - qty
        if seller_new_reserved < 0 or buyer_new_reserved < 0:
            raise LedgerError("order reservation underflow")
        now = time.time_ns()
        trade_cursor = self.conn.execute(
            """INSERT INTO exchange_trades
               (environment, pair, base_asset, quote_asset, qty_units, quote_units, price_num, price_den,
                fee_units, fee_asset, fee_bps, buyer_reserved_debit_units, buyer_release_units,
                buyer_user, seller_user, buy_order_id, sell_order_id, maker_order_id, taker_order_id, created_ns)
               VALUES ('DEMO', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                incoming["pair"], incoming["base_asset"], incoming["quote_asset"], qty, quote_cost,
                int(maker["limit_price_num"]), int(maker["limit_price_den"]),
                fee_units, buyer["quote_asset"], fee_bps, quote_debit, improvement,
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
        if fee_units:
            entries.append((f"system:DEMO_FEES:{buyer['quote_asset']}", buyer["quote_asset"], fee_units))
        self.ledger.post(
            f"exchange-trade:{trade_id}",
            "trade_settlement",
            f"trade:{trade_id}",
            entries,
            {
                "environment": DEMO_ENVIRONMENT,
                "trade_id": trade_id,
                "buy_order_id": int(buyer["id"]),
                "sell_order_id": int(seller["id"]),
                "principal_quote_units": quote_cost,
                "fee_units": fee_units,
                "fee_asset": buyer["quote_asset"],
                "demo_fee_bps_test_only": fee_bps,
                "buyer_reserved_debit_units": quote_debit,
                "buyer_release_units": improvement,
            },
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
                {"environment": DEMO_ENVIRONMENT, "order_id": int(order["id"]), "unspent_or_unfilled_units": remaining_hold},
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
        payload = {"environment": DEMO_ENVIRONMENT, "operation": "cancel_order", "user": user, "order_id": oid}
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
                        {"environment": DEMO_ENVIRONMENT, "order_id": oid},
                    )
                self.conn.execute(
                    "UPDATE exchange_orders SET reserved_units = 0, status = 'cancelled', cancel_reason = 'user_cancelled', updated_ns = ? WHERE id = ?",
                    (time.time_ns(), oid),
                )
                self._append_audit("order_cancelled", user, f"order:{oid}", {"environment": DEMO_ENVIRONMENT, "released_units": held})
            else:
                self._append_audit("order_cancel_noop", user, f"order:{oid}", {"status": order["status"]})
            result = {
                "environment": DEMO_ENVIRONMENT,
                "demo_only": True,
                "order": self._order_view(self._get_order(oid)),
                "test_only": True,
                "idempotent_replay": False,
            }
            self._remember_idempotency(user, key, request_hash, result)
            self.reconcile()
            return result

    def balances_snapshot(self, user: str) -> dict:
        user = self._user(user)
        prefix = f"user:DEMO:{user}:"
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
                result[asset] = {
                    "environment": DEMO_ENVIRONMENT,
                    "demo_only": True,
                    "test_only": True,
                    "available_units": free,
                    "reserved_units": reserved,
                }
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
                "environment": DEMO_ENVIRONMENT,
                "demo_only": True,
                "test_only": True,
                "price": {"num": int(order["limit_price_num"]), "den": int(order["limit_price_den"])},
                "qty_units": int(order["qty_units"]) - int(order["filled_units"]),
            }
        return {
            "environment": DEMO_ENVIRONMENT,
            "demo_only": True,
            "pair": pair,
            "asks": [view(o) for o in asks],
            "bids": [view(o) for o in bids],
            "test_only": True,
            "price_source": "submitted_limit_orders_only",
        }

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
        previous = GENESIS_HASH
        for row in rows:
            if str(row["previous_hash"]) != previous:
                raise LedgerError(f"exchange audit hash chain broken at {row['event_id']}")
            if str(row["environment"]) != DEMO_ENVIRONMENT:
                raise LedgerError(f"exchange audit event {row['event_id']} is not tagged DEMO")
            payload = json.loads(row["payload_json"])
            obj = {
                "environment": DEMO_ENVIRONMENT,
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
            for transaction in self.conn.execute("SELECT event_id, metadata_json FROM ledger_transactions"):
                metadata = json.loads(transaction["metadata_json"])
                if metadata.get("environment") != DEMO_ENVIRONMENT:
                    raise LedgerError(f"ledger event {transaction['event_id']} is not tagged DEMO")
            for entry in self.conn.execute("SELECT DISTINCT account, asset FROM ledger_entries"):
                account, asset = str(entry["account"]), str(entry["asset"])
                if asset not in DEMO_ASSETS:
                    raise LedgerError(f"non-DEMO asset found in exchange ledger: {asset}")
                parts = account.split(":")
                if parts[0] == "user":
                    if len(parts) != 5 or parts[1] != "DEMO" or parts[3] != asset or parts[4] not in ("available", "reserved"):
                        raise LedgerError(f"user ledger account is not DEMO-namespaced: {account}")
                    self._user(parts[2])
                elif parts[0] == "external":
                    if parts != ["external", "DEMO_CLEARING", asset]:
                        raise LedgerError(f"unknown DEMO clearing account: {account}")
                elif parts[0] == "system":
                    if asset != "DEMO_QUOTE" or parts != ["system", "DEMO_FEES", asset]:
                        raise LedgerError(f"unknown DEMO system account: {account}")
                else:
                    raise LedgerError(f"unrecognized exchange ledger account: {account}")
            expected: dict[tuple[str, str], int] = defaultdict(int)
            rows = self.conn.execute("SELECT * FROM exchange_orders ORDER BY id").fetchall()
            order_ids = set()
            orders_by_id = {}
            for row in rows:
                order = dict(row)
                order_id = int(order["id"])
                if order["environment"] != DEMO_ENVIRONMENT:
                    raise LedgerError(f"order {order_id} is not tagged DEMO")
                pair, base, quote = self._pair(order["pair"])
                if pair != order["pair"] or (base, quote) != (order["base_asset"], order["quote_asset"]):
                    raise LedgerError(f"order {order_id} has inconsistent DEMO assets")
                fee_bps = self._fee_bps(int(order["fee_bps"]))
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
                    required_hold = (self._limit_buy_reserve(int(order["limit_price_num"]),
                                                             int(order["limit_price_den"]), remaining, fee_bps)
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
                reserve_event = self.ledger.event(f"order-reserve:{order_id}")
                if not reserve_event:
                    raise LedgerError(f"order {order_id} lacks its reservation journal")
                if order["kind"] == "market":
                    initial_hold = (int(order["quote_budget_units"]) if order["side"] == "buy"
                                    else int(order["qty_units"]))
                elif order["side"] == "buy":
                    initial_hold = self._limit_buy_reserve(int(order["limit_price_num"]),
                                                           int(order["limit_price_den"]), qty, fee_bps)
                else:
                    initial_hold = qty
                reserve_asset = order["quote_asset"] if order["side"] == "buy" else order["base_asset"]
                initial_entries = {
                    (self._account(order["user"], reserve_asset, "available"), reserve_asset): -initial_hold,
                    (self._account(order["user"], reserve_asset, "reserved"), reserve_asset): initial_hold,
                }
                actual_initial_entries = {
                    (entry["account"], entry["asset"]): int(entry["amount_units"])
                    for entry in reserve_event["entries"]
                }
                if actual_initial_entries != initial_entries:
                    raise LedgerError(f"order {order_id} initial reservation journal does not match its request")

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
                if trade["environment"] != DEMO_ENVIRONMENT:
                    raise LedgerError(f"trade {trade_id} is not tagged DEMO")
                pair, base, quote = self._pair(trade["pair"])
                if pair != trade["pair"] or (base, quote) != (trade["base_asset"], trade["quote_asset"]):
                    raise LedgerError(f"trade {trade_id} has inconsistent DEMO assets")
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
                fee_bps = self._fee_bps(int(trade["fee_bps"]))
                fee_units = self._demo_fee_for_quote(quote_units, fee_bps)
                buyer_release = int(trade["buyer_release_units"])
                buyer_debit = int(trade["buyer_reserved_debit_units"])
                if fee_bps != int(buy_order["fee_bps"]):
                    raise LedgerError(f"trade {trade_id} fee does not match the buyer order")
                if str(trade["fee_asset"]) != quote or int(trade["fee_units"]) != fee_units:
                    raise LedgerError(f"trade {trade_id} DEMO fee is inconsistent")
                if buyer_release < 0 or buyer_debit != quote_units + fee_units + buyer_release:
                    raise LedgerError(f"trade {trade_id} buyer debit does not reconcile")
                expected_entries = {
                    (self._account(str(trade["seller_user"]), base, "reserved"), base): -qty,
                    (self._account(str(trade["buyer_user"]), base, "available"), base): qty,
                    (self._account(str(trade["buyer_user"]), quote, "reserved"), quote): -buyer_debit,
                    (self._account(str(trade["seller_user"]), quote, "available"), quote): quote_units,
                }
                if buyer_release:
                    expected_entries[(self._account(str(trade["buyer_user"]), quote, "available"), quote)] = buyer_release
                if fee_units:
                    expected_entries[(f"system:DEMO_FEES:{quote}", quote)] = fee_units
                settlement = self.ledger.event(f"exchange-trade:{trade_id}")
                if not settlement or settlement["event_type"] != "trade_settlement":
                    raise LedgerError(f"trade {trade_id} lacks its settlement journal")
                actual_entries = {
                    (entry["account"], entry["asset"]): int(entry["amount_units"])
                    for entry in settlement["entries"]
                }
                if actual_entries != expected_entries:
                    raise LedgerError(f"trade {trade_id} settlement entries do not match its trade row")
                metadata = settlement["metadata"]
                expected_metadata = {
                    "environment": DEMO_ENVIRONMENT,
                    "trade_id": trade_id,
                    "principal_quote_units": quote_units,
                    "fee_units": fee_units,
                    "fee_asset": quote,
                    "demo_fee_bps_test_only": fee_bps,
                    "buyer_reserved_debit_units": buyer_debit,
                    "buyer_release_units": buyer_release,
                }
                if any(metadata.get(key) != value for key, value in expected_metadata.items()):
                    raise LedgerError(f"trade {trade_id} settlement metadata does not reconcile")
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
            grant_rows = self.conn.execute("SELECT * FROM exchange_demo_funding ORDER BY created_ns, grant_id").fetchall()
            total_issued = 0
            for grant in grant_rows:
                grant_id = str(grant["grant_id"])
                user = self._user(str(grant["user"]))
                asset = str(grant["asset"])
                amount = int(grant["amount_units"])
                if grant["environment"] != DEMO_ENVIRONMENT or asset not in DEMO_ASSETS:
                    raise LedgerError(f"DEMO grant {grant_id} has an invalid environment or asset")
                if amount <= 0 or amount > MAX_DEMO_ACCOUNT_ASSET_UNITS:
                    raise LedgerError(f"DEMO grant {grant_id} exceeds its per-account cap")
                total_issued += amount
                event_id = str(grant["ledger_event_id"])
                event = self.ledger.event(event_id)
                if not event or event["event_type"] != "demo_funding_grant" or event["reference"] != event_id:
                    raise LedgerError(f"DEMO grant {grant_id} lacks its funding journal")
                expected_grant_entries = {
                    (f"external:DEMO_CLEARING:{asset}", asset): -amount,
                    (self._account(user, asset, "available"), asset): amount,
                }
                actual_grant_entries = {
                    (entry["account"], entry["asset"]): int(entry["amount_units"])
                    for entry in event["entries"]
                }
                if actual_grant_entries != expected_grant_entries:
                    raise LedgerError(f"DEMO grant {grant_id} journal does not match its grant record")
                metadata = event["metadata"]
                if any(metadata.get(key) != value for key, value in {
                    "environment": DEMO_ENVIRONMENT,
                    "demo_only": True,
                    "grant_id": grant_id,
                    "user": user,
                    "asset": asset,
                    "amount_units": amount,
                    "request_hash": str(grant["request_hash"]),
                }.items()):
                    raise LedgerError(f"DEMO grant {grant_id} journal metadata does not reconcile")
            if total_issued > MAX_DEMO_TOTAL_ISSUANCE_UNITS:
                raise LedgerError("historical DEMO issuance exceeds the fixed global cap")
            audit_count = self._verify_audit()
            return {
                "ledger_transactions": ledger_report["transactions"],
                "ledger_entries": ledger_report["entries"],
                "demo_funding_grants": len(grant_rows),
                "demo_issued_raw_units": total_issued,
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
