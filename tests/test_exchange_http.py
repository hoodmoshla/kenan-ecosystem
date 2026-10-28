"""HTTP integration tests for the closed, loopback-only DEMO exchange."""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.app as app
from backend.exchange.demo_funding import LocalDemoFunder
from backend.exchange.engine import Engine

PAIR = "DEMO_BASE/DEMO_QUOTE"


class TestExchangeHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.make_handler())
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.previous_engine = app.ENGINE
        self.previous_admin_token = os.environ.get("KENAN_ADMIN_TOKEN")
        self.engine = Engine()
        self.funder = LocalDemoFunder(self.engine.ledger)
        app.ENGINE = self.engine

    def tearDown(self):
        app.ENGINE = self.previous_engine
        self.engine.close()
        if self.previous_admin_token is None:
            os.environ.pop("KENAN_ADMIN_TOKEN", None)
        else:
            os.environ["KENAN_ADMIN_TOKEN"] = self.previous_admin_token

    def request_json(self, method, path, payload=None, headers=None, expected=200):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = dict(headers or {})
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                raw = response.read().decode("utf-8")
                self.assertEqual(response.status, expected)
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8")
            self.assertEqual(error.code, expected)
            return error.code, json.loads(raw) if raw else {}

    def get_json(self, path, headers=None):
        return self.request_json("GET", path, headers=headers)[1]

    def post_json(self, path, payload, headers=None):
        return self.request_json("POST", path, payload, headers=headers)[1]

    def assert_demo_response(self, response):
        self.assertEqual(response["environment"], "DEMO")
        self.assertIs(response["demo_only"], True)
        self.assertIs(response["test_only"], True)

    def test_empty_balance_snapshot_is_demo_tagged_and_has_no_fabricated_amounts(self):
        response = self.get_json("/exchange/balances/empty-user")
        self.assert_demo_response(response)
        self.assertEqual(response["user"], "empty-user")
        self.assertEqual(response["balances"], {})
        self.assertIn("not mapped to chain decimals", response["units"])

    def test_funding_withdrawal_and_funder_routes_are_not_available_over_http(self):
        for path in ("/exchange/deposit-demo", "/exchange/deposit", "/exchange/withdraw"):
            status, response = self.request_json("POST", path, {
                "user": "alice", "asset": "DEMO_BASE", "amount_units": 100,
            }, expected=410)
            self.assertEqual(status, 410)
            self.assert_demo_response(response)
            self.assertIn("unavailable", response["error"])
        status, response = self.request_json("POST", "/exchange/demo-funding", {
            "user": "alice", "asset": "DEMO_BASE", "amount_units": 100,
            "grant_id": "http-funding-attempt-01",
        }, expected=404)
        self.assertEqual(status, 404)
        self.assert_demo_response(response)
        self.assertEqual(self.engine.balances_snapshot("alice"), {})
        self.assertGreaterEqual(len(self.engine.audit_snapshot()), 4)

    def test_legacy_float_order_fields_missing_idempotency_and_real_symbols_are_rejected(self):
        _, body = self.request_json("POST", "/exchange/orders", {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty": 2.5, "price": 7.25,
        }, expected=400)
        self.assert_demo_response(body)
        self.assertIn("unsupported order fields", body["error"])
        self.assertEqual(self.engine.orders_snapshot(), [])
        _, body = self.request_json("POST", "/exchange/orders", {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 1, "price_num": 7, "price_den": 1,
        }, expected=400)
        self.assertIn("Idempotency-Key", body["error"])
        _, body = self.request_json("POST", "/exchange/orders", {
            "user": "seller", "side": "sell", "pair": "KEN/USD", "kind": "limit",
            "qty_units": 1, "price_num": 7, "price_den": 1,
        }, {"Idempotency-Key": "real-symbol-order-01"}, expected=400)
        self.assertIn("DEMO", body["error"])
        self.assertEqual(self.engine.orders_snapshot(), [])

    def test_limit_match_book_trades_and_http_idempotency(self):
        self.funder.grant("seller", "DEMO_BASE", 2, "http-seller-base-grant-01")
        self.funder.grant("buyer", "DEMO_QUOTE", 10, "http-buyer-quote-grant-01")
        ask_payload = {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 2, "price_num": 3, "price_den": 2,
        }
        ask = self.post_json("/exchange/orders", ask_payload, {"Idempotency-Key": "http-ask-key-0001"})
        self.assert_demo_response(ask)
        self.assertEqual(ask["order"]["status"], "open")
        book = self.get_json("/exchange/book?pair=" + urllib.parse.quote(PAIR, safe=""))
        self.assert_demo_response(book)
        self.assertEqual(book["price_source"], "submitted_limit_orders_only")
        self.assertEqual(book["asks"], [{
            "id": ask["order"]["id"], "environment": "DEMO", "demo_only": True, "test_only": True,
            "price": {"num": 3, "den": 2}, "qty_units": 2,
        }])
        bid_payload = {
            "user": "buyer", "side": "buy", "pair": PAIR, "kind": "limit",
            "qty_units": 2, "price_num": 2, "price_den": 1,
        }
        key = {"Idempotency-Key": "http-bid-key-0001"}
        bid = self.post_json("/exchange/orders", bid_payload, key)
        self.assert_demo_response(bid)
        self.assertEqual(bid["order"]["status"], "filled")
        self.assertEqual(len(bid["trades"]), 1)
        self.assertEqual(bid["trades"][0]["quote_units"], 3)
        self.assertEqual(bid["trades"][0]["price"], {"num": 3, "den": 2})
        self.assertEqual(bid["trades"][0]["environment"], "DEMO")
        replay = self.post_json("/exchange/orders", bid_payload, key)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["order"]["id"], bid["order"]["id"])
        self.assertEqual(len(self.engine.trades_snapshot()), 1)
        self.assert_demo_response(self.get_json("/exchange/orders?user=seller"))
        self.assert_demo_response(self.get_json("/exchange/trades?user=buyer"))
        self.assertEqual(self.get_json("/exchange/book?pair=DEMO_BASE%2FDEMO_QUOTE")["asks"], [])
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_cancel_releases_reservation_once_and_enforces_order_owner(self):
        self.funder.grant("seller", "DEMO_BASE", 3, "http-cancel-base-grant-01")
        order = self.post_json("/exchange/orders", {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 2, "price_num": 7, "price_den": 1,
        }, {"Idempotency-Key": "http-cancel-ask-key-01"})
        order_id = order["order"]["id"]
        _, error = self.request_json("POST", f"/exchange/orders/{order_id}/cancel", {
            "user": "other-user",
        }, {"Idempotency-Key": "wrong-owner-cancel-01"}, expected=403)
        self.assertIn("does not belong", error["error"])
        cancel_key = {"Idempotency-Key": "http-cancel-key-0001"}
        cancel = self.post_json(f"/exchange/orders/{order_id}/cancel", {"user": "seller"}, cancel_key)
        replay = self.post_json(f"/exchange/orders/{order_id}/cancel", {"user": "seller"}, cancel_key)
        self.assertEqual(cancel["order"]["status"], "cancelled")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(self.engine.balances_snapshot("seller")["DEMO_BASE"]["available_units"], 3)
        self.assertEqual(self.engine.balances_snapshot("seller")["DEMO_BASE"]["reserved_units"], 0)
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_exchange_loopback_guard_and_admin_token(self):
        self.assertTrue(app.exchange_client_allowed("127.0.0.1"))
        self.assertTrue(app.exchange_client_allowed("::1"))
        self.assertFalse(app.exchange_client_allowed("192.168.1.20"))
        self.assertFalse(app.exchange_client_allowed("0.0.0.0"))
        os.environ["KENAN_ADMIN_TOKEN"] = "correct-test-token"
        status, _ = self.request_json("GET", "/admin/status", headers={"X-Admin-Token": "wrong"}, expected=403)
        self.assertEqual(status, 403)
        status, response = self.request_json("GET", "/admin/status", headers={"X-Admin-Token": "correct-test-token"})
        self.assertEqual(status, 200)
        self.assertEqual(response["open_orders"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
