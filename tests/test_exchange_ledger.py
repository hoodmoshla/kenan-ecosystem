"""Persistence, accounting, concurrency, and recovery tests for the test exchange."""
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.exchange.engine import (
    Engine, ExchangeError, IdempotencyConflict, InsufficientFunds,
)
from backend.exchange.ledger import DoubleEntryLedger, LedgerError


class TestExchangeLedger(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(allow_test_funding=True)

    def tearDown(self):
        self.engine.close()

    def fund(self, user, asset, amount, key=None):
        return self.engine.issue_test_fixture(user, asset, amount, key or f"fixture-{user}-{asset}-01")

    def test_default_exchange_has_no_balances_and_fixture_funding_is_off(self):
        closed = Engine()
        try:
            self.assertEqual(closed.balances_snapshot("alice"), {})
            with self.assertRaises(ExchangeError):
                closed.issue_test_fixture("alice", "KEN", 100, "fixture-disabled-01")
            with self.assertRaises(InsufficientFunds):
                closed.place("alice", "sell", "KEN/USD", "limit", 1, 1, 1,
                             idempotency_key="unfunded-order-01")
            self.assertEqual(closed.orders_snapshot(), [])
            self.assertEqual(closed.trades_snapshot(), [])
        finally:
            closed.close()

    def test_limit_reservation_partial_fill_then_cancel_reconciles(self):
        self.fund("seller1", "KEN", 2)
        self.fund("seller2", "KEN", 2)
        self.fund("buyer", "USD", 40)
        first = self.engine.place("seller1", "sell", "KEN/USD", "limit", 2, 3, 1,
                                  idempotency_key="seller-one-0001")
        second = self.engine.place("seller2", "sell", "KEN/USD", "limit", 2, 3, 1,
                                   idempotency_key="seller-two-0001")
        self.assertEqual(first["order"]["status"], "open")
        self.assertEqual(second["order"]["status"], "open")

        result = self.engine.place("buyer", "buy", "KEN/USD", "limit", 5, 4, 1,
                                   idempotency_key="buyer-limit-0001")
        self.assertEqual(result["order"]["status"], "partially_filled")
        self.assertEqual(result["order"]["filled_units"], 4)
        self.assertEqual(result["order"]["remaining_units"], 1)
        self.assertEqual(result["order"]["reserved_units"], 4)
        self.assertEqual(len(result["trades"]), 2)
        self.assertEqual(sum(t["quote_units"] for t in result["trades"]), 12)
        self.assertEqual(self.engine.balances_snapshot("buyer"), {
            "KEN": {"available_units": 4, "reserved_units": 0},
            "USD": {"available_units": 24, "reserved_units": 4},
        })

        cancelled = self.engine.cancel(result["order"]["id"], "buyer", "cancel-buyer-0001")
        self.assertEqual(cancelled["order"]["status"], "cancelled")
        self.assertEqual(self.engine.balances_snapshot("buyer"), {
            "KEN": {"available_units": 4, "reserved_units": 0},
            "USD": {"available_units": 28, "reserved_units": 0},
        })
        self.assertEqual(self.engine.reconcile()["trades"], 2)
        self.assertEqual(result["fee_policy"], "unconfigured_no_fee_charged")

    def test_market_buy_uses_real_book_budget_and_returns_unspent_units(self):
        self.fund("seller", "KEN", 3)
        self.fund("buyer", "USD", 10)
        ask = self.engine.place("seller", "sell", "KEN/USD", "limit", 3, 3, 2,
                                idempotency_key="rational-ask-001")
        self.assertEqual(ask["order"]["status"], "open")
        result = self.engine.place("buyer", "buy", "KEN/USD", "market", 3,
                                   quote_budget_units=4, idempotency_key="market-buy-0001")
        self.assertEqual(result["order"]["status"], "cancelled")
        self.assertEqual(result["order"]["filled_units"], 2)
        self.assertEqual(result["order"]["remaining_units"], 1)
        self.assertEqual(result["order"]["reserved_units"], 0)
        self.assertEqual(result["order"]["cancel_reason"], "market_remainder_not_resting")
        self.assertEqual(result["trades"][0]["price"], {"num": 3, "den": 2})
        self.assertEqual(result["trades"][0]["quote_units"], 3)
        self.assertEqual(self.engine.balances_snapshot("buyer"), {
            "KEN": {"available_units": 2, "reserved_units": 0},
            "USD": {"available_units": 7, "reserved_units": 0},
        })
        self.assertEqual(self.engine.book("KEN/USD")["asks"][0]["qty_units"], 1)
        self.assertEqual(self.engine.reconcile()["ok"], True)

    def test_price_time_priority_uses_best_price_then_earliest_order(self):
        self.fund("seller_a", "KEN", 1)
        self.fund("seller_b", "KEN", 1)
        self.fund("seller_c", "KEN", 1)
        self.fund("buyer", "USD", 8)
        # The worst ask is posted first; equal best asks are posted B then C.
        self.engine.place("seller_a", "sell", "KEN/USD", "limit", 1, 4, 1,
                          idempotency_key="priority-worse-ask-01")
        self.engine.place("seller_b", "sell", "KEN/USD", "limit", 1, 3, 1,
                          idempotency_key="priority-best-ask-01")
        self.engine.place("seller_c", "sell", "KEN/USD", "limit", 1, 3, 1,
                          idempotency_key="priority-tied-ask-01")
        result = self.engine.place("buyer", "buy", "KEN/USD", "limit", 2, 4, 1,
                                   idempotency_key="priority-buy-order-01")
        self.assertEqual([trade["seller"] for trade in result["trades"]], ["seller_b", "seller_c"])
        self.assertEqual([trade["quote_units"] for trade in result["trades"]], [3, 3])
        book = self.engine.book("KEN/USD")
        self.assertEqual(book["asks"][0]["price"], {"num": 4, "den": 1})
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_market_sell_matches_a_submitted_limit_bid(self):
        self.fund("buyer", "USD", 3)
        self.fund("seller", "KEN", 2)
        self.engine.place("buyer", "buy", "KEN/USD", "limit", 2, 3, 2,
                          idempotency_key="market-sell-bid-01")
        result = self.engine.place("seller", "sell", "KEN/USD", "market", 2,
                                   idempotency_key="market-sell-001")
        self.assertEqual(result["order"]["status"], "filled")
        self.assertEqual(result["trades"][0]["quote_units"], 3)
        self.assertEqual(self.engine.balances_snapshot("seller"), {
            "USD": {"available_units": 3, "reserved_units": 0},
        })
        self.assertEqual(self.engine.balances_snapshot("buyer"), {
            "KEN": {"available_units": 2, "reserved_units": 0},
        })

    def test_exact_rational_price_never_rounds_fractional_quote_units(self):
        self.fund("seller", "KEN", 3)
        self.fund("buyer", "USD", 1)
        self.engine.place("seller", "sell", "KEN/USD", "limit", 3, 1, 3,
                          idempotency_key="thirds-ask-0001")
        result = self.engine.place("buyer", "buy", "KEN/USD", "limit", 3, 1, 3,
                                   idempotency_key="thirds-bid-0001")
        self.assertEqual(result["order"]["status"], "filled")
        self.assertEqual(result["trades"][0]["quote_units"], 1)
        self.assertEqual(result["trades"][0]["price"], {"num": 1, "den": 3})
        self.assertEqual(self.engine.reconcile()["ok"], True)

    def test_idempotency_replay_and_conflicting_payload(self):
        self.fund("seller", "KEN", 4)
        first = self.engine.place("seller", "sell", "KEN/USD", "limit", 3, 2, 1,
                                  idempotency_key="same-order-key-01")
        replay = self.engine.place("seller", "sell", "KEN/USD", "limit", 3, 2, 1,
                                   idempotency_key="same-order-key-01")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["order"]["id"], first["order"]["id"])
        self.assertEqual(len(self.engine.orders_snapshot()), 1)
        with self.assertRaises(IdempotencyConflict):
            self.engine.place("seller", "sell", "KEN/USD", "limit", 2, 2, 1,
                              idempotency_key="same-order-key-01")
        before = self.engine.ledger.verify()["transactions"]
        cancelled = self.engine.cancel(first["order"]["id"], "seller", "cancel-order-key-01")
        replay_cancel = self.engine.cancel(first["order"]["id"], "seller", "cancel-order-key-01")
        self.assertEqual(cancelled["order"]["status"], "cancelled")
        self.assertTrue(replay_cancel["idempotent_replay"])
        self.assertEqual(self.engine.ledger.verify()["transactions"], before + 1)
        self.assertEqual(self.engine.balances_snapshot("seller")["KEN"], {
            "available_units": 4, "reserved_units": 0,
        })

    def test_concurrent_reservations_cannot_overdraw(self):
        self.fund("seller", "KEN", 10)

        def submit(index):
            try:
                return self.engine.place(
                    "seller", "sell", "KEN/USD", "limit", 1, 1, 1,
                    idempotency_key=f"concurrent-order-{index:04d}",
                )
            except InsufficientFunds:
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(submit, range(16)))
        succeeded = [outcome for outcome in outcomes if outcome is not None]
        self.assertEqual(len(succeeded), 10)
        self.assertEqual(len(self.engine.orders_snapshot("seller")), 10)
        self.assertEqual(self.engine.balances_snapshot("seller"), {
            "KEN": {"available_units": 0, "reserved_units": 10},
        })
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_idempotency_is_serialized_across_separate_database_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "shared-exchange.sqlite3")
            first = Engine(path, allow_test_funding=True)
            second = Engine(path)
            try:
                first.issue_test_fixture("seller", "KEN", 2, "multi-conn-fund-01")
                barrier = threading.Barrier(2)

                def submit(engine):
                    barrier.wait()
                    return engine.place("seller", "sell", "KEN/USD", "limit", 1, 2, 1,
                                        idempotency_key="multi-conn-idempotency-01")

                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(submit, (first, second)))
                self.assertEqual(sum(bool(response["idempotent_replay"]) for response in responses), 1)
                self.assertEqual(first.counts()["orders"], 1)
                self.assertEqual(first.balances_snapshot("seller")["KEN"], {
                    "available_units": 1, "reserved_units": 1,
                })
                self.assertTrue(first.reconcile()["ok"])
                self.assertTrue(second.reconcile()["ok"])
            finally:
                second.close()
                first.close()

    def test_cancel_vs_match_race_has_one_atomic_outcome(self):
        self.fund("seller", "KEN", 2)
        self.fund("buyer", "USD", 6)
        ask = self.engine.place("seller", "sell", "KEN/USD", "limit", 2, 3, 1,
                                idempotency_key="race-ask-order-01")
        barrier = threading.Barrier(2)

        def cancel():
            barrier.wait()
            return self.engine.cancel(ask["order"]["id"], "seller", "race-cancel-key-01")

        def match():
            barrier.wait()
            return self.engine.place("buyer", "buy", "KEN/USD", "limit", 2, 3, 1,
                                     idempotency_key="race-bid-order-01")

        with ThreadPoolExecutor(max_workers=2) as pool:
            cancel_future = pool.submit(cancel)
            match_future = pool.submit(match)
            cancel_future.result()
            match_future.result()

        seller_order = self.engine.orders_snapshot("seller")[0]
        trades = self.engine.trades_snapshot()
        self.assertIn(seller_order["status"], ("cancelled", "filled"))
        self.assertLessEqual(len(trades), 1)
        if seller_order["status"] == "filled":
            self.assertEqual(len(trades), 1)
            self.assertEqual(self.engine.balances_snapshot("buyer")["KEN"]["available_units"], 2)
            self.assertEqual(self.engine.balances_snapshot("seller")["USD"]["available_units"], 6)
        else:
            self.assertEqual(trades, [])
            self.assertEqual(self.engine.balances_snapshot("seller")["KEN"], {
                "available_units": 2, "reserved_units": 0,
            })
            self.assertEqual(self.engine.balances_snapshot("buyer")["USD"], {
                "available_units": 0, "reserved_units": 6,
            })
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_fault_during_fill_rolls_back_order_ledger_trade_and_audit(self):
        self.fund("seller", "KEN", 1)
        self.fund("buyer", "USD", 10)
        self.engine.place("seller", "sell", "KEN/USD", "limit", 1, 2, 1,
                          idempotency_key="rollback-ask-001")
        orders_before = self.engine.orders_snapshot()
        trades_before = self.engine.trades_snapshot()
        audit_before = len(self.engine.audit_snapshot())
        ledger_before = self.engine.ledger.verify()["transactions"]
        buyer_before = self.engine.balances_snapshot("buyer")
        with patch.object(self.engine, "_settle_fill", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.engine.place("buyer", "buy", "KEN/USD", "limit", 1, 2, 1,
                                  idempotency_key="rollback-bid-001")
        self.assertEqual(self.engine.orders_snapshot(), orders_before)
        self.assertEqual(self.engine.trades_snapshot(), trades_before)
        self.assertEqual(len(self.engine.audit_snapshot()), audit_before)
        self.assertEqual(self.engine.ledger.verify()["transactions"], ledger_before)
        self.assertEqual(self.engine.balances_snapshot("buyer"), buyer_before)

    def test_restart_recovery_preserves_orders_trades_ledger_and_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "exchange.sqlite3")
            first = Engine(path, allow_test_funding=True)
            first.issue_test_fixture("seller", "KEN", 4, "restart-fixture-01")
            first.issue_test_fixture("buyer", "USD", 20, "restart-fixture-02")
            ask = first.place("seller", "sell", "KEN/USD", "limit", 4, 3, 2,
                              idempotency_key="restart-ask-0001")
            bid = first.place("buyer", "buy", "KEN/USD", "limit", 2, 2, 1,
                              idempotency_key="restart-bid-0001")
            self.assertEqual(bid["order"]["status"], "filled")
            self.assertEqual(len(bid["trades"]), 1)
            first.close()

            recovered = Engine(path)
            self.assertEqual(recovered.trades_snapshot(), bid["trades"])
            self.assertEqual(recovered.orders_snapshot("seller")[0]["status"], "partially_filled")
            self.assertEqual(recovered.balances_snapshot("seller"), {
                "KEN": {"available_units": 0, "reserved_units": 2},
                "USD": {"available_units": 3, "reserved_units": 0},
            })
            self.assertEqual(recovered.balances_snapshot("buyer"), {
                "KEN": {"available_units": 2, "reserved_units": 0},
                "USD": {"available_units": 17, "reserved_units": 0},
            })
            replay = recovered.place("buyer", "buy", "KEN/USD", "limit", 2, 2, 1,
                                     idempotency_key="restart-bid-0001")
            self.assertTrue(replay["idempotent_replay"])
            self.assertEqual(len(recovered.trades_snapshot()), 1)
            recovered.cancel(ask["order"]["id"], "seller", "restart-cancel-001")
            self.assertEqual(recovered.balances_snapshot("seller")["KEN"], {
                "available_units": 2, "reserved_units": 0,
            })
            self.assertTrue(recovered.reconcile()["ok"])
            recovered.close()

    def test_float_amounts_and_prices_are_rejected(self):
        self.fund("seller", "KEN", 5)
        with self.assertRaises(ExchangeError):
            self.engine.place("seller", "sell", "KEN/USD", "limit", 1.5, 2, 1,
                              idempotency_key="float-qty-0001")
        with self.assertRaises(ExchangeError):
            self.engine.place("seller", "sell", "KEN/USD", "limit", 1, 2.5, 1,
                              idempotency_key="float-price-001")
        with self.assertRaises(ExchangeError):
            self.engine.place("seller", "buy", "KEN/USD", "market", 1,
                              price_den=1.0, quote_budget_units=1,
                              idempotency_key="float-market-den-01")
        with self.assertRaises(ExchangeError):
            self.engine.place("seller", "buy", "KEN/USD", "market", 1,
                              idempotency_key="market-no-budget-1")
        self.assertEqual(self.engine.orders_snapshot(), [])

    def test_ledger_balancing_immutability_and_hash_chain(self):
        ledger = DoubleEntryLedger()
        try:
            with self.assertRaises(LedgerError):
                with ledger.transaction():
                    ledger.post("bad-event", "test", "bad", [("external", "KEN", -5)])
            self.assertEqual(ledger.verify()["transactions"], 0)
            with ledger.transaction():
                ledger.post("good-event", "test", "good", [
                    ("external:test", "KEN", -5), ("user:alice:KEN:available", "KEN", 5),
                ])
            self.assertEqual(ledger.balance("user:alice:KEN:available", "KEN"), 5)
            with self.assertRaises(sqlite3.IntegrityError):
                with ledger.transaction():
                    ledger.conn.execute("UPDATE ledger_entries SET amount_units = 6 WHERE ledger_seq = 1")
            self.assertEqual(ledger.verify()["entries"], 2)
        finally:
            ledger.close()

    def test_hash_chain_detects_out_of_band_database_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tampered.sqlite3")
            engine = Engine(path, allow_test_funding=True)
            engine.issue_test_fixture("alice", "KEN", 5, "tamper-fixture-01")
            engine.close()
            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER ledger_entries_no_update")
            connection.execute("UPDATE ledger_entries SET amount_units = amount_units + 1 WHERE seq = 1")
            connection.commit()
            connection.close()
            with self.assertRaises(LedgerError):
                Engine(path)

    def test_audit_is_hash_chained_and_immutable(self):
        self.fund("seller", "KEN", 1)
        self.engine.place("seller", "sell", "KEN/USD", "limit", 1, 2, 1,
                          idempotency_key="audit-order-0001")
        report = self.engine.reconcile()
        self.assertGreaterEqual(report["audit_events"], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.engine.ledger.transaction():
                self.engine.conn.execute("UPDATE exchange_audit SET event_type = 'changed' WHERE seq = 1")
        self.assertTrue(self.engine.reconcile()["ok"])


if __name__ == "__main__":
    unittest.main()
