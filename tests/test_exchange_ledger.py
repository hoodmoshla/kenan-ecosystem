"""Persistence, double-entry accounting, capped DEMO funding, concurrency and recovery."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.exchange import demo_funding
from backend.exchange.demo_funding import DemoFundingError, LocalDemoFunder
from backend.exchange.engine import (
    Engine,
    ExchangeError,
    IdempotencyConflict,
    InsufficientFunds,
)
from backend.exchange.ledger import DoubleEntryLedger, LedgerError
from backend.exchange.schema import (
    DEMO_ASSETS,
    DEMO_ENVIRONMENT,
    MAX_DEMO_ACCOUNT_ASSET_UNITS,
    MAX_DEMO_TOTAL_ISSUANCE_UNITS,
)

PAIR = "DEMO_BASE/DEMO_QUOTE"


class TestExchangeLedger(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.funder = LocalDemoFunder(self.engine.ledger)

    def tearDown(self):
        self.engine.close()

    def fund(self, user, asset, amount, grant_id=None):
        grant_id = grant_id or f"grant-{user}-{asset}-01"
        return self.funder.grant(user, asset, amount, grant_id)

    def amounts(self, user):
        snapshot = self.engine.balances_snapshot(user)
        return {
            asset: (balance["available_units"], balance["reserved_units"])
            for asset, balance in snapshot.items()
        }

    def test_fresh_engine_is_empty_and_only_allowlisted_demo_pair_is_accepted(self):
        self.assertEqual(self.engine.balances_snapshot("alice"), {})
        with self.assertRaises(ExchangeError):
            self.engine.place("alice", "sell", "KEN/USD", "limit", 1, 1, 1,
                              idempotency_key="unfunded-order-01")
        with self.assertRaises(ExchangeError):
            self.engine.place("alice", "sell", "DEMO_BASE/KEN", "limit", 1, 1, 1,
                              idempotency_key="bad-asset-order-01")
        with self.assertRaises(ExchangeError):
            self.engine.place("alice", "sell", PAIR, "limit", 1, 1.0, 1,
                              idempotency_key="float-price-order-01")
        self.assertEqual(self.engine.orders_snapshot(), [])
        self.assertEqual(self.engine.trades_snapshot(), [])
        self.assertEqual(self.engine.counts(), {"open_orders": 0, "orders": 0, "trades": 0})

    def test_funding_is_local_demo_capped_idempotent_and_audited(self):
        first = self.fund("alice", "DEMO_BASE", 100, "grant-alice-base-01")
        transactions = self.engine.ledger.verify()["transactions"]
        replay = self.fund("alice", "DEMO_BASE", 100, "grant-alice-base-01")
        self.assertTrue(first["demo_only"])
        self.assertEqual(first["environment"], DEMO_ENVIRONMENT)
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(self.engine.ledger.verify()["transactions"], transactions)
        self.assertEqual(self.amounts("alice"), {"DEMO_BASE": (100, 0)})
        with self.assertRaises(DemoFundingError):
            self.funder.grant("alice", "DEMO_BASE", 101, "grant-alice-base-01")
        with self.assertRaises(DemoFundingError):
            self.funder.grant("alice", "DEMO_BASE", 100, "grant-alice-base-02")
        with self.assertRaises(DemoFundingError):
            self.funder.grant("alice", "KEN", 100, "grant-real-asset-01")
        with self.assertRaises(DemoFundingError):
            self.funder.grant("alice", "DEMO_QUOTE", 100.5, "grant-float-amount-01")
        grant_event = self.engine.ledger.event("demo-funding:grant-alice-base-01")
        self.assertEqual(grant_event["event_type"], "demo_funding_grant")
        self.assertEqual(grant_event["metadata"]["environment"], "DEMO")
        self.assertTrue(all(entry["asset"] in DEMO_ASSETS for entry in grant_event["entries"]))
        audit_types = [row["event_type"] for row in self.engine.audit_snapshot()]
        self.assertIn("demo_funding_grant", audit_types)
        self.assertIn("demo_funding_replayed", audit_types)
        self.assertIn("demo_funding_rejected", audit_types)
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.conn.execute(
                "UPDATE exchange_demo_funding SET amount_units = 1 WHERE grant_id = ?",
                ("grant-alice-base-01",),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.conn.execute(
                "DELETE FROM exchange_demo_funding WHERE grant_id = ?",
                ("grant-alice-base-01",),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.conn.execute(
                "UPDATE ledger_entries SET amount_units = 1 WHERE account LIKE 'user:DEMO:alice:%'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.conn.execute(
                "DELETE FROM ledger_entries WHERE account LIKE 'user:DEMO:alice:%'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.conn.execute(
                "DELETE FROM ledger_transactions WHERE event_id = ?",
                ("demo-funding:grant-alice-base-01",),
            )
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_local_cli_grant_is_readable_after_restart_by_engine_on_same_demo_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "separate-demo.sqlite3")
            output = io.StringIO()
            with patch.object(demo_funding, "DEFAULT_DEMO_DB", database), patch("sys.stdout", output):
                exit_code = demo_funding.main([
                    "grant", "--user", "cli-user", "--asset", "DEMO_QUOTE",
                    "--amount-units", "250", "--grant-id", "local-cli-grant-01",
                    "--i-understand-demo-only",
                ])
            self.assertEqual(exit_code, 0)
            grant_result = json.loads(output.getvalue())
            self.assertEqual(grant_result["environment"], "DEMO")
            self.assertEqual(grant_result["asset"], "DEMO_QUOTE")
            self.assertEqual(grant_result["amount_units"], 250)
            recovered = Engine(database)
            try:
                self.assertEqual(recovered.balances_snapshot("cli-user")["DEMO_QUOTE"]["available_units"], 250)
                self.assertEqual(recovered.ledger.verify()["transactions"], 1)
                self.assertTrue(recovered.reconcile()["ok"])
            finally:
                recovered.close()

    def test_fixed_per_account_and_global_issuance_caps(self):
        with self.assertRaises(DemoFundingError):
            self.funder.grant(
                "over-cap", "DEMO_BASE", MAX_DEMO_ACCOUNT_ASSET_UNITS + 1,
                "grant-per-account-over-cap-01",
            )
        issued = 0
        account_index = 0
        while issued < MAX_DEMO_TOTAL_ISSUANCE_UNITS:
            amount = min(MAX_DEMO_ACCOUNT_ASSET_UNITS, MAX_DEMO_TOTAL_ISSUANCE_UNITS - issued)
            self.funder.grant(
                f"capuser{account_index}", "DEMO_BASE", amount,
                f"grant-global-cap-{account_index:03d}",
            )
            issued += amount
            account_index += 1
        self.assertEqual(issued, MAX_DEMO_TOTAL_ISSUANCE_UNITS)
        with self.assertRaises(DemoFundingError):
            self.funder.grant("beyond-global", "DEMO_BASE", 1, "grant-global-over-01")
        report = self.engine.reconcile()
        self.assertEqual(report["demo_issued_raw_units"], MAX_DEMO_TOTAL_ISSUANCE_UNITS)
        self.assertEqual(report["demo_funding_grants"], account_index)
        self.assertTrue(report["ok"])

    def test_complete_limit_cycle_partial_fills_fees_reservations_and_cancel_release(self):
        self.fund("seller1", "DEMO_BASE", 2)
        self.fund("seller2", "DEMO_BASE", 2)
        self.fund("buyer", "DEMO_QUOTE", 40)
        first = self.engine.place("seller1", "sell", PAIR, "limit", 2, 3, 1,
                                  idempotency_key="seller-one-order-001")
        second = self.engine.place("seller2", "sell", PAIR, "limit", 2, 3, 1,
                                   idempotency_key="seller-two-order-001")
        result = self.engine.place("buyer", "buy", PAIR, "limit", 5, 4, 1,
                                   idempotency_key="buyer-partial-order-001")
        self.assertEqual(first["order"]["status"], "open")
        self.assertEqual(second["order"]["status"], "open")
        self.assertEqual(result["order"]["status"], "partially_filled")
        self.assertEqual(result["order"]["filled_units"], 4)
        self.assertEqual(result["order"]["remaining_units"], 1)
        self.assertEqual(result["order"]["reserved_units"], 4)
        self.assertEqual(len(result["trades"]), 2)
        self.assertEqual(sum(trade["quote_units"] for trade in result["trades"]), 12)
        self.assertTrue(all(trade["environment"] == "DEMO" and trade["demo_only"] for trade in result["trades"]))
        self.assertEqual(self.amounts("buyer"), {"DEMO_BASE": (4, 0), "DEMO_QUOTE": (24, 4)})
        canceled = self.engine.cancel(result["order"]["id"], "buyer", "cancel-buyer-order-001")
        self.assertEqual(canceled["order"]["status"], "cancelled")
        self.assertEqual(self.amounts("buyer"), {"DEMO_BASE": (4, 0), "DEMO_QUOTE": (28, 0)})
        self.assertEqual(self.engine.reconcile()["trades"], 2)
        self.assertEqual(result["fee_policy"], "unconfigured_no_fee_charged")
        self.assertEqual(self.engine.book(PAIR)["price_source"], "submitted_limit_orders_only")
        self.assertEqual(self.engine.book(PAIR)["asks"], [])

    def test_market_buy_matches_submitted_book_and_releases_unspent_budget(self):
        self.fund("seller", "DEMO_BASE", 3)
        self.fund("buyer", "DEMO_QUOTE", 10)
        ask = self.engine.place("seller", "sell", PAIR, "limit", 3, 3, 2,
                                idempotency_key="rational-ask-order-001")
        result = self.engine.place("buyer", "buy", PAIR, "market", 3,
                                   quote_budget_units=4, idempotency_key="market-buy-order-001")
        self.assertEqual(ask["order"]["status"], "open")
        self.assertEqual(result["order"]["status"], "cancelled")
        self.assertEqual(result["order"]["filled_units"], 2)
        self.assertEqual(result["order"]["remaining_units"], 1)
        self.assertEqual(result["order"]["reserved_units"], 0)
        self.assertEqual(result["trades"][0]["price"], {"num": 3, "den": 2})
        self.assertEqual(result["trades"][0]["quote_units"], 3)
        self.assertEqual(self.amounts("buyer"), {"DEMO_BASE": (2, 0), "DEMO_QUOTE": (7, 0)})
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_market_sell_and_rational_price_keep_integer_asset_units(self):
        self.fund("buyer", "DEMO_QUOTE", 3)
        self.fund("seller", "DEMO_BASE", 2)
        self.engine.place("buyer", "buy", PAIR, "limit", 2, 3, 2,
                          idempotency_key="market-sell-bid-order-01")
        result = self.engine.place("seller", "sell", PAIR, "market", 2,
                                   idempotency_key="market-sell-order-001")
        self.assertEqual(result["order"]["status"], "filled")
        self.assertEqual(result["trades"][0]["quote_units"], 3)
        self.assertEqual(self.amounts("seller"), {"DEMO_QUOTE": (3, 0)})
        self.assertEqual(self.amounts("buyer"), {"DEMO_BASE": (2, 0)})
        self.assertTrue(self.engine.reconcile()["ok"])

        self.fund("thirds-seller", "DEMO_BASE", 3)
        self.fund("thirds-buyer", "DEMO_QUOTE", 1)
        self.engine.place("thirds-seller", "sell", PAIR, "limit", 3, 1, 3,
                          idempotency_key="thirds-ask-order-001")
        thirds = self.engine.place("thirds-buyer", "buy", PAIR, "limit", 3, 1, 3,
                                   idempotency_key="thirds-bid-order-001")
        self.assertEqual(thirds["order"]["status"], "filled")
        self.assertEqual(thirds["trades"][0]["quote_units"], 1)
        self.assertEqual(thirds["trades"][0]["price"], {"num": 1, "den": 3})
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_demo_fee_is_reserved_charged_and_reconciled_only_in_demo(self):
        fee_engine = Engine(demo_fee_bps=100)
        fee_funder = LocalDemoFunder(fee_engine.ledger)
        try:
            fee_funder.grant("seller", "DEMO_BASE", 100, "grant-fee-seller-001")
            fee_funder.grant("buyer", "DEMO_QUOTE", 1_010, "grant-fee-buyer-001")
            fee_engine.place("seller", "sell", PAIR, "limit", 100, 10, 1,
                             idempotency_key="fee-seller-ask-order-01")
            buy = fee_engine.place("buyer", "buy", PAIR, "limit", 100, 10, 1,
                                   idempotency_key="fee-buyer-bid-order-01")
            trade = buy["trades"][0]
            self.assertEqual(trade["quote_units"], 1_000)
            self.assertEqual(trade["fee_units"], 10)
            self.assertEqual(trade["fee_asset"], "DEMO_QUOTE")
            self.assertEqual(trade["demo_fee_bps_test_only"], 100)
            self.assertEqual(fee_engine.ledger.balance("system:DEMO_FEES:DEMO_QUOTE", "DEMO_QUOTE"), 10)
            self.assertEqual(fee_engine.balances_snapshot("buyer"), {
                "DEMO_BASE": {"environment": "DEMO", "demo_only": True, "test_only": True,
                              "available_units": 100, "reserved_units": 0},
            })
            report = fee_engine.reconcile()
            self.assertTrue(report["ok"])
            trade_event = fee_engine.ledger.event(f"exchange-trade:{trade['id']}")
            self.assertEqual(trade_event["metadata"]["fee_units"], 10)
            self.assertEqual(trade_event["metadata"]["environment"], "DEMO")
        finally:
            fee_engine.close()

    def test_market_buy_budget_includes_demo_fee_and_cannot_overdraw(self):
        fee_engine = Engine(demo_fee_bps=100)
        fee_funder = LocalDemoFunder(fee_engine.ledger)
        try:
            fee_funder.grant("seller", "DEMO_BASE", 100, "grant-market-fee-seller-01")
            fee_funder.grant("buyer", "DEMO_QUOTE", 1_005, "grant-market-fee-buyer-01")
            fee_engine.place("seller", "sell", PAIR, "limit", 100, 10, 1,
                             idempotency_key="market-fee-ask-order-001")
            result = fee_engine.place("buyer", "buy", PAIR, "market", 100,
                                      quote_budget_units=1_005, idempotency_key="market-fee-buy-order-001")
            self.assertEqual(len(result["trades"]), 1)
            self.assertEqual(result["trades"][0]["qty_units"], 99)
            self.assertEqual(result["trades"][0]["quote_units"], 990)
            self.assertEqual(result["trades"][0]["fee_units"], 9)
            self.assertEqual(result["order"]["status"], "cancelled")
            self.assertEqual(result["order"]["filled_units"], 99)
            self.assertEqual(fee_engine.balances_snapshot("buyer")["DEMO_QUOTE"]["available_units"], 6)
            self.assertEqual(fee_engine.ledger.balance("system:DEMO_FEES:DEMO_QUOTE", "DEMO_QUOTE"), 9)
            self.assertTrue(fee_engine.reconcile()["ok"])
        finally:
            fee_engine.close()

    def test_order_idempotency_replay_conflict_and_cancel_release_once(self):
        self.fund("seller", "DEMO_BASE", 4)
        first = self.engine.place("seller", "sell", PAIR, "limit", 3, 2, 1,
                                  idempotency_key="same-order-key-0001")
        replay = self.engine.place("seller", "sell", PAIR, "limit", 3, 2, 1,
                                   idempotency_key="same-order-key-0001")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["order"]["id"], first["order"]["id"])
        self.assertEqual(len(self.engine.orders_snapshot()), 1)
        with self.assertRaises(IdempotencyConflict):
            self.engine.place("seller", "sell", PAIR, "limit", 2, 2, 1,
                              idempotency_key="same-order-key-0001")
        before = self.engine.ledger.verify()["transactions"]
        canceled = self.engine.cancel(first["order"]["id"], "seller", "cancel-order-key-0001")
        replay_cancel = self.engine.cancel(first["order"]["id"], "seller", "cancel-order-key-0001")
        self.assertEqual(canceled["order"]["status"], "cancelled")
        self.assertTrue(replay_cancel["idempotent_replay"])
        self.assertEqual(self.engine.ledger.verify()["transactions"], before + 1)
        self.assertEqual(self.amounts("seller"), {"DEMO_BASE": (4, 0)})
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_concurrent_reservations_cannot_overdraw(self):
        self.fund("seller", "DEMO_BASE", 10)
        def submit(index):
            try:
                return self.engine.place(
                    "seller", "sell", PAIR, "limit", 1, 1, 1,
                    idempotency_key=f"concurrent-order-{index:04d}",
                )
            except InsufficientFunds:
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(submit, range(16)))
        succeeded = [outcome for outcome in outcomes if outcome is not None]
        self.assertEqual(len(succeeded), 10)
        self.assertEqual(len(self.engine.orders_snapshot("seller")), 10)
        self.assertEqual(self.amounts("seller"), {"DEMO_BASE": (0, 10)})
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_concurrent_funding_replays_once_across_database_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "demo-exchange.sqlite3")
            first = Engine(path)
            second = Engine(path)
            funder1, funder2 = LocalDemoFunder(first.ledger), LocalDemoFunder(second.ledger)
            barrier = threading.Barrier(2)
            def grant(funder):
                barrier.wait()
                return funder.grant("shared", "DEMO_BASE", 250, "grant-shared-base-0001")
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(grant, (funder1, funder2)))
                self.assertEqual(sum(result["idempotent_replay"] for result in results), 1)
                self.assertEqual(first.ledger.verify()["transactions"], 1)
                self.assertEqual(first.balances_snapshot("shared")["DEMO_BASE"]["available_units"], 250)
                self.assertTrue(first.reconcile()["ok"])
                self.assertTrue(second.reconcile()["ok"])
            finally:
                second.close()
                first.close()

    def test_concurrent_different_grants_cannot_fund_account_asset_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "demo-exchange.sqlite3")
            first, second = Engine(path), Engine(path)
            funders = (LocalDemoFunder(first.ledger), LocalDemoFunder(second.ledger))
            barrier = threading.Barrier(2)
            def grant(item):
                funder, grant_id = item
                barrier.wait()
                try:
                    return funder.grant("single", "DEMO_QUOTE", 10, grant_id)
                except DemoFundingError:
                    return None
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(grant, ((funders[0], "grant-single-quote-001"),
                                                    (funders[1], "grant-single-quote-002"))))
                self.assertEqual(sum(result is not None for result in results), 1)
                self.assertEqual(first.balances_snapshot("single")["DEMO_QUOTE"]["available_units"], 10)
                self.assertTrue(first.reconcile()["ok"])
            finally:
                second.close()
                first.close()

    def test_order_idempotency_is_serialized_across_separate_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "demo-exchange.sqlite3")
            first, second = Engine(path), Engine(path)
            try:
                LocalDemoFunder(first.ledger).grant("seller", "DEMO_BASE", 2, "grant-multi-conn-base-01")
                barrier = threading.Barrier(2)
                def submit(engine):
                    barrier.wait()
                    return engine.place("seller", "sell", PAIR, "limit", 1, 2, 1,
                                        idempotency_key="multi-conn-order-key-01")
                with ThreadPoolExecutor(max_workers=2) as pool:
                    responses = list(pool.map(submit, (first, second)))
                self.assertEqual(sum(bool(response["idempotent_replay"]) for response in responses), 1)
                self.assertEqual(first.counts()["orders"], 1)
                self.assertEqual(self.amounts_for(first, "seller"), {"DEMO_BASE": (1, 1)})
                self.assertTrue(first.reconcile()["ok"])
                self.assertTrue(second.reconcile()["ok"])
            finally:
                second.close()
                first.close()

    @staticmethod
    def amounts_for(engine, user):
        snapshot = engine.balances_snapshot(user)
        return {asset: (value["available_units"], value["reserved_units"])
                for asset, value in snapshot.items()}

    def test_cancel_vs_match_race_has_one_atomic_outcome(self):
        self.fund("seller", "DEMO_BASE", 2)
        self.fund("buyer", "DEMO_QUOTE", 6)
        ask = self.engine.place("seller", "sell", PAIR, "limit", 2, 3, 1,
                                idempotency_key="race-ask-order-0001")
        barrier = threading.Barrier(2)
        def cancel():
            barrier.wait()
            return self.engine.cancel(ask["order"]["id"], "seller", "race-cancel-key-01")
        def match():
            barrier.wait()
            return self.engine.place("buyer", "buy", PAIR, "limit", 2, 3, 1,
                                     idempotency_key="race-bid-order-0001")
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
            self.assertEqual(self.engine.balances_snapshot("buyer")["DEMO_BASE"]["available_units"], 2)
            self.assertEqual(self.engine.balances_snapshot("seller")["DEMO_QUOTE"]["available_units"], 6)
        else:
            self.assertEqual(trades, [])
            self.assertEqual(self.amounts("seller"), {"DEMO_BASE": (2, 0)})
            self.assertEqual(self.amounts("buyer"), {"DEMO_QUOTE": (0, 6)})
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_fault_during_fill_rolls_back_order_ledger_trade_and_audit(self):
        self.fund("seller", "DEMO_BASE", 1)
        self.fund("buyer", "DEMO_QUOTE", 10)
        self.engine.place("seller", "sell", PAIR, "limit", 1, 2, 1,
                          idempotency_key="rollback-ask-order-01")
        orders_before = self.engine.orders_snapshot()
        trades_before = self.engine.trades_snapshot()
        audit_before = len(self.engine.audit_snapshot())
        ledger_before = self.engine.ledger.verify()["transactions"]
        buyer_before = self.engine.balances_snapshot("buyer")
        with patch.object(self.engine, "_settle_fill", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.engine.place("buyer", "buy", PAIR, "limit", 1, 2, 1,
                                  idempotency_key="rollback-bid-order-01")
        self.assertEqual(self.engine.orders_snapshot(), orders_before)
        self.assertEqual(self.engine.trades_snapshot(), trades_before)
        self.assertEqual(len(self.engine.audit_snapshot()), audit_before)
        self.assertEqual(self.engine.ledger.verify()["transactions"], ledger_before)
        self.assertEqual(self.engine.balances_snapshot("buyer"), buyer_before)
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_restart_recovers_open_reservations_then_cancel_and_trade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "durable-demo-exchange.sqlite3")
            before = Engine(path, demo_fee_bps=100)
            LocalDemoFunder(before.ledger).grant("seller", "DEMO_BASE", 100, "grant-restart-seller-01")
            LocalDemoFunder(before.ledger).grant("buyer", "DEMO_QUOTE", 2_000, "grant-restart-buyer-01")
            seller_order = before.place("seller", "sell", PAIR, "limit", 100, 10, 1,
                                        idempotency_key="restart-ask-order-0001")
            buyer_order = before.place("buyer", "buy", PAIR, "limit", 100, 9, 1,
                                       idempotency_key="restart-bid-order-0001")
            self.assertEqual(buyer_order["order"]["status"], "open")
            report_before = before.reconcile()
            self.assertTrue(report_before["ok"])
            before.close()

            after = Engine(path)
            try:
                self.assertEqual(after.orders_snapshot("seller")[0]["status"], "open")
                self.assertEqual(after.orders_snapshot("buyer")[0]["status"], "open")
                self.assertEqual(after.balances_snapshot("seller")["DEMO_BASE"]["reserved_units"], 100)
                self.assertEqual(after.balances_snapshot("buyer")["DEMO_QUOTE"]["reserved_units"], 909)
                self.assertTrue(after.reconcile()["ok"])
                canceled = after.cancel(buyer_order["order"]["id"], "buyer", "restart-cancel-key-01")
                self.assertEqual(canceled["order"]["status"], "cancelled")
                filled = after.place("buyer", "buy", PAIR, "limit", 100, 10, 1,
                                     idempotency_key="restart-crossing-bid-0001")
                self.assertEqual(filled["order"]["status"], "filled")
                self.assertEqual(filled["trades"][0]["fee_units"], 0)  # new Engine fee config is 0; stored order terms remain immutable
                self.assertTrue(after.reconcile()["ok"])
            finally:
                after.close()

    def test_schema_fails_closed_if_immutable_funding_trigger_was_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "tampered-schema.sqlite3")
            engine = Engine(path)
            try:
                engine.conn.execute("DROP TRIGGER exchange_demo_funding_no_update")
            finally:
                engine.close()
            with self.assertRaises(Exception):
                Engine(path)


class TestDoubleEntryLedger(unittest.TestCase):
    def test_journals_must_balance_and_user_available_cannot_go_negative(self):
        ledger = DoubleEntryLedger()
        try:
            with ledger.transaction():
                with self.assertRaises(LedgerError):
                    ledger.post("bad-unbalanced", "test", "test", [("external:x", "DEMO_BASE", -1)])
            with ledger.transaction():
                ledger.post("fund", "test", "fund", [("external:x", "DEMO_BASE", -10),
                                                        ("user:DEMO:alice:DEMO_BASE:available", "DEMO_BASE", 10)],
                            {"environment": "DEMO"})
            with ledger.transaction():
                with self.assertRaises(LedgerError):
                    ledger.post("overdraw", "test", "overdraw", [
                        ("user:DEMO:alice:DEMO_BASE:available", "DEMO_BASE", -11),
                        ("external:x", "DEMO_BASE", 11),
                    ], {"environment": "DEMO"})
            self.assertEqual(ledger.balance("user:DEMO:alice:DEMO_BASE:available", "DEMO_BASE"), 10)
            self.assertEqual(ledger.verify()["transactions"], 1)
        finally:
            ledger.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
