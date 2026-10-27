"""HTTP integration tests for the in-memory DEMO exchange endpoints."""
import json
import os
import sys
import threading
import unittest
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backend.app as app
from backend.exchange.engine import Engine


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
        self.previous_audit = app.audit
        app.ENGINE = Engine()
        app.audit = lambda event, detail: None

    def tearDown(self):
        app.ENGINE = self.previous_engine
        app.audit = self.previous_audit

    def get_json(self, path):
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path, payload):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            return json.loads(response.read().decode("utf-8"))

    def assert_demo_only(self, response):
        self.assertIs(response.get("demo_only"), True)

    def test_empty_balance_snapshot_is_demo_only(self):
        response = self.get_json("/exchange/balances/empty-user")
        self.assert_demo_only(response)
        self.assertEqual(response["user"], "empty-user")
        self.assertEqual(response["balances"], {})

    def test_deposit_is_demo_only_and_returned_in_snapshot(self):
        deposited = self.post_json(
            "/exchange/deposit-demo",
            {"user": "demo-user", "asset": "USD-DEMO", "amount": 25.5},
        )
        self.assert_demo_only(deposited)
        self.assertEqual(deposited["balances"]["USD-DEMO"], {"free": 25.5, "reserved": 0.0})

        snapshot = self.get_json("/exchange/balances/demo-user")
        self.assert_demo_only(snapshot)
        self.assertEqual(snapshot["balances"], deposited["balances"])

    def test_order_creation_book_read_and_cancel_are_demo_only(self):
        deposited = self.post_json(
            "/exchange/deposit-demo",
            {"user": "seller", "asset": "KEN", "amount": 3},
        )
        self.assert_demo_only(deposited)

        created = self.post_json(
            "/exchange/orders",
            {
                "user": "seller",
                "side": "sell",
                "pair": "KEN/USD-DEMO",
                "kind": "limit",
                "qty": 2,
                "price": 7.25,
            },
        )
        self.assert_demo_only(created)
        self.assertEqual(created["status"], "open")

        book = self.get_json("/exchange/book?pair=" + urllib.parse.quote("KEN/USD-DEMO", safe=""))
        self.assert_demo_only(book)
        self.assertEqual(book["asks"], [{"id": created["id"], "price": 7.25, "qty": 2.0}])

        orders = self.get_json("/exchange/orders?user=seller")
        self.assert_demo_only(orders)
        self.assertEqual([order["id"] for order in orders["orders"]], [created["id"]])

        cancelled = self.post_json(
            f"/exchange/orders/{created['id']}/cancel",
            {"user": "seller"},
        )
        self.assert_demo_only(cancelled)
        self.assertEqual(cancelled["status"], "cancelled")

        trades = self.get_json("/exchange/trades")
        self.assert_demo_only(trades)
        self.assertEqual(trades["trades"], [])


if __name__ == "__main__":
    unittest.main()
