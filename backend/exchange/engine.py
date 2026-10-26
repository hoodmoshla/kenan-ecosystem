"""Kenan Exchange — matching engine (DEMO balances only).

Separation rule: demo exchange balances live ONLY in this in-memory backend
engine and are labelled DEMO in every response. They are never chain balances
and never real money. Balances/orders disappear when the process restarts.
Settlement to the chain is NOT performed by this engine; that step is listed
as pending in docs/STATUS.md.

Rules: price-time priority, limit + market orders, partial fills, reserved
balances (locked on order placement, released on fill/cancel), fee = 0.1%
of the quote amount on each fill, recorded per trade. No order is ever
reported as filled unless a trade record was created by match().
"""
from __future__ import annotations

import itertools
import math
import threading
from dataclasses import dataclass, field

FEE_RATE = 0.001  # 0.1% demo fee


@dataclass
class Order:
    id: int
    user: str
    side: str  # buy | sell
    pair: str
    kind: str  # limit | market
    price: float | None
    qty: float
    filled: float = 0.0
    status: str = "open"

    @property
    def remaining(self):
        return round(self.qty - self.filled, 10)


@dataclass
class Engine:
    balances: dict = field(default_factory=dict)  # user -> {asset: free, reserved}
    orders: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def _bal(self, user, asset):
        return self.balances.setdefault(user, {}).setdefault(asset, {"free": 0.0, "reserved": 0.0})

    def deposit_demo(self, user, asset, amount):
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            raise ValueError("demo deposit must be a finite positive amount")
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError("demo deposit must be a finite positive amount")
        with self._lock:
            b = self._bal(user, asset)
            b["free"] = round(b["free"] + amount, 10)

    def book(self, pair):
        with self._lock:
            buys = sorted([o for o in self.orders if o.pair == pair and o.side == "buy" and o.status == "open"],
                          key=lambda o: (-(o.price or 0), o.id))
            sells = sorted([o for o in self.orders if o.pair == pair and o.side == "sell" and o.status == "open"],
                           key=lambda o: ((o.price or 0), o.id))
            return {"pair": pair, "bids": [{"id": o.id, "price": o.price, "qty": o.remaining} for o in buys],
                    "asks": [{"id": o.id, "price": o.price, "qty": o.remaining} for o in sells],
                    "demo_only": True}

    def place(self, user, side, pair, kind, qty, price=None):
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            raise ValueError("quantity must be a finite positive number")
        if not math.isfinite(qty) or qty <= 0:
            raise ValueError("quantity must be positive")
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy or sell")
        if kind not in ("limit", "market"):
            raise ValueError("kind must be limit or market")
        if not isinstance(pair, str) or pair.count("/") != 1:
            raise ValueError("pair must be BASE/QUOTE")
        base, quote = pair.split("/")
        if not base or not quote or base == quote:
            raise ValueError("pair must contain two different assets")
        if price is not None:
            try:
                price = float(price)
            except (TypeError, ValueError):
                raise ValueError("price must be finite and positive")
            if not math.isfinite(price) or price <= 0:
                raise ValueError("price must be finite and positive")
        if kind == "limit" and price is None:
            raise ValueError("limit order needs a positive price")
        with self._lock:
            return self._place(user, side, pair, kind, qty, price)

    def _place(self, user, side, pair, kind, qty, price):
        base, quote = pair.split("/")
        if side == "buy" and kind == "limit":
            need = qty * price
            b = self._bal(user, quote)
            if b["free"] < need:
                raise ValueError("insufficient DEMO quote balance")
            b["free"], b["reserved"] = round(b["free"] - need, 10), round(b["reserved"] + need, 10)
        if side == "sell":
            b = self._bal(user, base)
            if b["free"] < qty:
                raise ValueError("insufficient DEMO base balance")
            b["free"], b["reserved"] = round(b["free"] - qty, 10), round(b["reserved"] + qty, 10)
        order = Order(id=next(self._ids), user=user, side=side, pair=pair,
                      kind=kind, price=price, qty=qty)
        self.orders.append(order)
        self._match(order)
        return order

    def _match(self, taker):
        base, quote = taker.pair.split("/")
        makers = [o for o in self.orders if o.pair == taker.pair and o.status == "open"
                  and o.id != taker.id and o.side != taker.side]
        if taker.side == "buy":
            makers.sort(key=lambda o: ((o.price or 0), o.id))
        else:
            makers.sort(key=lambda o: (-(o.price or 0), o.id))
        for maker in makers:
            if taker.remaining <= 0:
                break
            if taker.kind == "limit" and maker.price is not None:
                if taker.side == "buy" and taker.price < maker.price:
                    break
                if taker.side == "sell" and taker.price > maker.price:
                    break
            price = maker.price
            if price is None:
                continue  # market-vs-market needs a reference price: not executed
            qty = min(taker.remaining, maker.remaining)
            cost = round(qty * price, 10)
            fee = round(cost * FEE_RATE, 10)
            # settle reserved balances
            if taker.side == "buy":
                tb, mb = self._bal(taker.user, quote), self._bal(maker.user, base)
                # taker reserved only for limit buys; market buys check at fill
                if taker.kind == "limit":
                    reserved_for_fill = round(qty * taker.price, 10)
                    tb["reserved"] = round(tb["reserved"] - reserved_for_fill, 10)
                    tb["free"] = round(tb["free"] + reserved_for_fill - cost, 10)
                else:
                    if tb["free"] < cost:
                        continue
                    tb["free"] = round(tb["free"] - cost, 10)
                mb["reserved"] = round(mb["reserved"] - qty, 10)
                self._bal(taker.user, base)["free"] = round(self._bal(taker.user, base)["free"] + qty, 10)
                self._bal(maker.user, quote)["free"] = round(self._bal(maker.user, quote)["free"] + cost - fee, 10)
            else:
                tb, mb = self._bal(taker.user, base), self._bal(maker.user, quote)
                tb["reserved"] = round(tb["reserved"] - qty, 10)
                if maker.kind == "limit":
                    mb["reserved"] = round(mb["reserved"] - cost, 10)
                else:
                    if mb["free"] < cost:
                        continue
                    mb["free"] = round(mb["free"] - cost, 10)
                self._bal(taker.user, quote)["free"] = round(self._bal(taker.user, quote)["free"] + cost - fee, 10)
                self._bal(maker.user, base)["free"] = round(self._bal(maker.user, base)["free"] + qty, 10)
            taker.filled = round(taker.filled + qty, 10)
            maker.filled = round(maker.filled + qty, 10)
            if maker.remaining <= 0:
                maker.status = "filled"
            self.trades.append({"taker_order": taker.id, "maker_order": maker.id,
                                "pair": taker.pair, "price": price, "qty": qty,
                                "fee_quote": fee, "demo_only": True})
        if taker.remaining <= 0:
            taker.status = "filled"
        elif taker.kind == "market":
            # unfilled market remainder: release any reservation, cancel
            taker.status = "cancelled-unfilled"
        # limit remainder stays open with its reservation intact

    def cancel(self, order_id, user):
        with self._lock:
            return self._cancel(order_id, user)

    def _cancel(self, order_id, user):
        for o in self.orders:
            if o.id == order_id and o.user == user and o.status == "open":
                base, quote = o.pair.split("/")
                if o.side == "buy" and o.kind == "limit":
                    b = self._bal(user, quote)
                    refund = round(o.remaining * o.price, 10)
                    b["reserved"], b["free"] = round(b["reserved"] - refund, 10), round(b["free"] + refund, 10)
                if o.side == "sell":
                    b = self._bal(user, base)
                    b["reserved"], b["free"] = round(b["reserved"] - o.remaining, 10), round(b["free"] + o.remaining, 10)
                o.status = "cancelled"
                return o
        raise ValueError("open order not found for this user")
