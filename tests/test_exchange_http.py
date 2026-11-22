"""HTTP tests for the authenticated, loopback-only DEMO exchange."""
import json
import os
import sys
import threading
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.app as app
from backend.exchange.auth import ExchangeAuth
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
        self.previous_auth = app.AUTH
        self.previous_admin_token = os.environ.get("KENAN_ADMIN_TOKEN")
        self.engine = Engine()
        self.funder = LocalDemoFunder(self.engine.ledger)
        self.auth = ExchangeAuth(self.engine.ledger)
        self.tokens = {}
        app.ENGINE = self.engine
        app.AUTH = self.auth

    def tearDown(self):
        app.ENGINE = self.previous_engine
        app.AUTH = self.previous_auth
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

    def get_json(self, path, headers=None, expected=200):
        return self.request_json("GET", path, headers=headers, expected=expected)[1]

    def post_json(self, path, payload, headers=None, expected=200):
        return self.request_json("POST", path, payload, headers=headers, expected=expected)[1]

    def auth_headers(self, user):
        if user not in self.tokens:
            password = f"local test password for {user} only"
            self.auth.register(user, password)
            self.tokens[user] = self.auth.authenticate(user, password)["token"]
        return {"Authorization": f"Bearer {self.tokens[user]}"}

    def assert_demo_response(self, response):
        self.assertEqual(response["environment"], "DEMO")
        self.assertIs(response["demo_only"], True)
        self.assertIs(response["test_only"], True)

    def test_registration_login_session_revocation_and_token_hash_storage(self):
        password = "local test password for registration"
        created = self.post_json("/exchange/register", {"username": "alice", "password": password}, expected=201)
        self.assertEqual(created["account"]["user"], "alice")
        self.assertEqual(created["account"]["role"], "trader")
        _, duplicate = self.request_json("POST", "/exchange/register", {"username": "alice", "password": password}, expected=400)
        self.assertNotIn(password, json.dumps(duplicate))
        _, invalid = self.request_json("POST", "/exchange/login", {"username": "alice", "password": "wrong password"}, expected=401)
        self.assertEqual(invalid["error"], "invalid username or password")
        logged_in = self.post_json("/exchange/login", {"username": "alice", "password": password})
        token = logged_in["session"]["token"]
        self.assertEqual(self.get_json("/exchange/me", {"Authorization": f"Bearer {token}"})["user"], "alice")
        stored = self.engine.conn.execute("SELECT token_hash FROM exchange_sessions").fetchone()[0]
        import hashlib
        self.assertEqual(stored, hashlib.sha256(token.encode("ascii")).hexdigest())
        self.assertNotIn(token, stored)
        self.post_json("/exchange/logout", {}, {"Authorization": f"Bearer {token}"})
        self.get_json("/exchange/me", {"Authorization": f"Bearer {token}"}, expected=401)

    def test_exchange_requires_authentication_and_isolates_balance_orders_and_trades(self):
        self.get_json("/exchange/balances/alice", expected=401)
        self.get_json("/exchange/orders?user=alice", expected=401)
        self.request_json("POST", "/exchange/orders", {
            "user": "alice", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 1, "price_num": 1, "price_den": 1,
        }, {"Idempotency-Key": "unauth-order-key-001"}, expected=401)
        alice_headers = self.auth_headers("alice")
        bob_headers = self.auth_headers("bob")
        self.funder.grant("alice", "DEMO_BASE", 2, "isolation-alice-grant-01")
        _, error = self.request_json("GET", "/exchange/balances/alice", headers=bob_headers, expected=403)
        self.assertIn("another account", error["error"])
        self.request_json("GET", "/exchange/orders?user=alice", headers=bob_headers, expected=403)
        self.request_json("GET", "/exchange/trades?user=alice", headers=bob_headers, expected=403)
        self.request_json("POST", "/exchange/orders", {
            "user": "alice", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 1, "price_num": 1, "price_den": 1,
        }, {**bob_headers, "Idempotency-Key": "spoof-order-key-001"}, expected=403)
        self.assertEqual(self.get_json("/exchange/balances/bob", headers=bob_headers)["balances"], {})
        self.assertEqual(self.get_json("/exchange/balances/alice", headers=alice_headers)["balances"]["DEMO_BASE"]["available_units"], 2)
        self.assertEqual(self.engine.orders_snapshot(), [])

    def test_read_only_book_is_public_but_account_read_routes_are_private(self):
        book = self.get_json("/exchange/book?pair=" + urllib.parse.quote(PAIR, safe=""))
        self.assert_demo_response(book)
        self.get_json("/exchange/orders", headers=self.auth_headers("reader"))

    def test_non_trader_role_cannot_submit_orders(self):
        self.auth.create_account("auditor", "local auditor test password", role="auditor")
        token = self.auth.authenticate("auditor", "local auditor test password")["token"]
        self.request_json("POST", "/exchange/orders", {
            "side": "sell", "pair": PAIR, "kind": "limit", "qty_units": 1,
            "price_num": 1, "price_den": 1,
        }, {"Authorization": f"Bearer {token}", "Idempotency-Key": "auditor-order-01"}, expected=403)
        self.assertEqual(self.engine.orders_snapshot(), [])

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
        }, self.auth_headers("alice"), expected=404)
        self.assertEqual(status, 404)
        self.assert_demo_response(response)
        self.assertEqual(self.engine.balances_snapshot("alice"), {})
        self.assertGreaterEqual(len(self.engine.audit_snapshot()), 4)

    def test_legacy_float_order_fields_missing_idempotency_and_real_symbols_are_rejected(self):
        headers = self.auth_headers("seller")
        _, body = self.request_json("POST", "/exchange/orders", {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty": 2.5, "price": 7.25,
        }, {**headers, "Idempotency-Key": "legacy-field-key-01"}, expected=400)
        self.assert_demo_response(body)
        self.assertIn("unsupported order fields", body["error"])
        self.assertEqual(self.engine.orders_snapshot(), [])
        _, body = self.request_json("POST", "/exchange/orders", {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 1, "price_num": 7, "price_den": 1,
        }, headers, expected=400)
        self.assertIn("Idempotency-Key", body["error"])
        _, body = self.request_json("POST", "/exchange/orders", {
            "user": "seller", "side": "sell", "pair": "KEN/USD", "kind": "limit",
            "qty_units": 1, "price_num": 7, "price_den": 1,
        }, {**headers, "Idempotency-Key": "real-symbol-order-01"}, expected=400)
        self.assertIn("DEMO", body["error"])
        self.assertEqual(self.engine.orders_snapshot(), [])

    def test_limit_match_book_trades_and_http_idempotency(self):
        self.funder.grant("seller", "DEMO_BASE", 2, "http-seller-base-grant-01")
        self.funder.grant("buyer", "DEMO_QUOTE", 10, "http-buyer-quote-grant-01")
        seller_headers = self.auth_headers("seller")
        buyer_headers = self.auth_headers("buyer")
        ask_payload = {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 2, "price_num": 3, "price_den": 2,
        }
        ask = self.post_json("/exchange/orders", ask_payload, {**seller_headers, "Idempotency-Key": "http-ask-key-0001"})
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
        key = {**buyer_headers, "Idempotency-Key": "http-bid-key-0001"}
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
        self.assert_demo_response(self.get_json("/exchange/orders?user=seller", headers=seller_headers))
        self.assert_demo_response(self.get_json("/exchange/trades?user=buyer", headers=buyer_headers))
        self.assertEqual(self.get_json("/exchange/book?pair=DEMO_BASE%2FDEMO_QUOTE")["asks"], [])
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_cancel_releases_reservation_once_and_enforces_session_owner(self):
        self.funder.grant("seller", "DEMO_BASE", 3, "http-cancel-base-grant-01")
        seller_headers = self.auth_headers("seller")
        other_headers = self.auth_headers("other-user")
        order = self.post_json("/exchange/orders", {
            "user": "seller", "side": "sell", "pair": PAIR, "kind": "limit",
            "qty_units": 2, "price_num": 7, "price_den": 1,
        }, {**seller_headers, "Idempotency-Key": "http-cancel-ask-key-01"})
        order_id = order["order"]["id"]
        _, error = self.request_json("POST", f"/exchange/orders/{order_id}/cancel", {
            "user": "seller",
        }, {**other_headers, "Idempotency-Key": "wrong-owner-cancel-01"}, expected=403)
        self.assertIn("another account", error["error"])
        cancel_key = {**seller_headers, "Idempotency-Key": "http-cancel-key-0001"}
        cancel = self.post_json(f"/exchange/orders/{order_id}/cancel", {"user": "seller"}, cancel_key)
        replay = self.post_json(f"/exchange/orders/{order_id}/cancel", {"user": "seller"}, cancel_key)
        self.assertEqual(cancel["order"]["status"], "cancelled")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(self.engine.balances_snapshot("seller")["DEMO_BASE"]["available_units"], 3)
        self.assertEqual(self.engine.balances_snapshot("seller")["DEMO_BASE"]["reserved_units"], 0)
        self.assertTrue(self.engine.reconcile()["ok"])

    def test_authenticated_demo_cycle_partial_fill_cancel_and_restart_reconciles(self):
        previous_engine, previous_auth = app.ENGINE, app.AUTH
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "exchange.sqlite3")
            engine = Engine(path)
            auth = ExchangeAuth(engine.ledger)
            try:
                app.ENGINE, app.AUTH = engine, auth
                funder = LocalDemoFunder(engine.ledger)
                funder.grant("seller1", "DEMO_BASE", 1, "e2e-seller-one-grant-01")
                funder.grant("seller2", "DEMO_BASE", 1, "e2e-seller-two-grant-01")
                funder.grant("buyer", "DEMO_QUOTE", 20, "e2e-buyer-quote-grant-01")
                tokens = {}
                for user in ("seller1", "seller2", "buyer"):
                    password = f"e2e password for {user} only"
                    auth.register(user, password)
                    tokens[user] = auth.authenticate(user, password)["token"]
                seller1_headers = {"Authorization": f"Bearer {tokens['seller1']}", "Idempotency-Key": "e2e-seller-one-ask-01"}
                seller2_headers = {"Authorization": f"Bearer {tokens['seller2']}", "Idempotency-Key": "e2e-seller-two-ask-01"}
                self.post_json("/exchange/orders", {
                    "side": "sell", "pair": PAIR, "kind": "limit",
                    "qty_units": 1, "price_num": 3, "price_den": 1,
                }, seller1_headers)
                self.post_json("/exchange/orders", {
                    "side": "sell", "pair": PAIR, "kind": "limit",
                    "qty_units": 1, "price_num": 3, "price_den": 1,
                }, seller2_headers)
                buyer_headers = {"Authorization": f"Bearer {tokens['buyer']}", "Idempotency-Key": "e2e-buyer-bid-0001"}
                partial = self.post_json("/exchange/orders", {
                    "side": "buy", "pair": PAIR, "kind": "limit",
                    "qty_units": 3, "price_num": 4, "price_den": 1,
                }, buyer_headers)
                self.assertEqual(partial["order"]["status"], "partially_filled")
                self.assertEqual(partial["order"]["filled_units"], 2)
                self.assertEqual(len(partial["trades"]), 2)
                order_id = partial["order"]["id"]
                self.post_json(f"/exchange/orders/{order_id}/cancel", {}, {
                    "Authorization": f"Bearer {tokens['buyer']}",
                    "Idempotency-Key": "e2e-buyer-cancel-01",
                })
                self.assertTrue(engine.reconcile()["ok"])
                self.assertEqual(engine.ledger.verify()["transactions"], 9)

                engine.close()
                recovered = Engine(path)
                app.ENGINE, app.AUTH = recovered, ExchangeAuth(recovered.ledger)
                try:
                    self.assertTrue(recovered.reconcile()["ok"])
                    self.assertEqual(recovered.counts()["trades"], 2)
                    self.assertEqual(recovered.orders_snapshot("buyer")[0]["status"], "cancelled")
                    buyer = recovered.balances_snapshot("buyer")
                    self.assertEqual(buyer["DEMO_BASE"]["available_units"], 2)
                    self.assertEqual(buyer["DEMO_QUOTE"]["reserved_units"], 0)
                    self.assertEqual(recovered.ledger.verify()["transactions"], 9)
                finally:
                    recovered.close()
            finally:
                # ``engine`` may already have been closed before its restart.
                if engine.conn:
                    try:
                        engine.close()
                    except Exception:
                        pass
                app.ENGINE, app.AUTH = previous_engine, previous_auth

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
