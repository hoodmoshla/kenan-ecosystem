"""Real tests for Kenan core — run: python3 -m unittest discover -s tests -v
Uses only test keys/phrases generated in-process; nothing real, nothing
persisted outside a temp dir. Network tests spin the node + backend on
ephemeral ports.
"""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from blockchain.kenan_chain import Chain, UNIT, BASE_FEE, verify_signature  # noqa: E402
from blockchain.node_server import make_handler as node_handler  # noqa: E402
from backend.app import make_handler as backend_handler  # noqa: E402
from backend.exchange.engine import Engine  # noqa: E402
from backend.modules.features import (  # noqa: E402
    shield_check, intelligence_answer, draft_transfer, Circle,
    create_invoice, receipt_for, academy_account, insights, Governance,
    bridge_transfer, RecoveryProposal)

_spec = importlib.util.spec_from_file_location(
    "kenan_wallet", os.path.join(ROOT, "wallet-core", "wallet.py"))
wallet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wallet)


def post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


class TestWallet(unittest.TestCase):
    def test_create_restore_and_backup_proof(self):
        w = wallet.create_wallet()
        self.assertEqual(len(w["mnemonic"].split()), 24)
        self.assertTrue(w["address"].startswith("kenan1"))
        self.assertTrue(wallet.verify_restore(w["mnemonic"], w["address"]))
        self.assertFalse(wallet.verify_restore(w["mnemonic"], "kenan1" + "0" * 40))
        with self.assertRaises(ValueError):
            wallet.restore_wallet("abandon " * 23 + "about")  # bad checksum/order

    def test_sign_verify_and_tamper(self):
        w = wallet.create_wallet()
        tx = wallet.sign_transfer(w, "kenan1" + "a" * 40, 1000, BASE_FEE, 0)
        self.assertTrue(verify_signature(tx))
        bad = dict(tx, amount=999)
        self.assertFalse(verify_signature(bad))

    def test_known_bip39_devnet_vector(self):
        w = wallet.restore_wallet("abandon " * 23 + "art")
        tx = wallet.sign_transfer(w, "kenan1" + "a" * 40, 123456789, BASE_FEE, 0)
        self.assertEqual(w["address"], "kenan1e28c3608979d45d2c7dc74b1c19519e54ce8961c")
        self.assertEqual(tx["signature"], "83996074af8b16499df9e5c75fce1de46e9495ed50585d5ed26548c949a50ed26f278e5b938df56ed63dd1bb109e00fa5f2504304b8bb18e5de48408a980e40b")


class TestChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.chain = Chain(path=os.path.join(self.tmp.name, "chain.json"))
        self.alice = wallet.create_wallet()
        self.bob = wallet.create_wallet()
        self.chain.faucet(self.alice["address"], 1000 * UNIT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_transfer_fees_burn_and_nonce(self):
        before = self.chain.info()["height"]
        tx = wallet.sign_transfer(self.alice, self.bob["address"], 100 * UNIT, BASE_FEE, 0)
        res = self.chain.submit(tx)
        self.assertEqual(res["status"], "confirmed")
        self.assertEqual(self.chain.info()["height"], before + 1)
        self.assertEqual(self.chain.balance(self.bob["address"]), 100 * UNIT)
        self.assertEqual(self.chain.balance(self.alice["address"]),
                         1000 * UNIT - 100 * UNIT - BASE_FEE)
        self.assertEqual(self.chain.burned, BASE_FEE // 2)
        # replay + wrong nonce + insufficient must all fail
        with self.assertRaises(ValueError):
            self.chain.submit(tx)
        with self.assertRaises(ValueError):
            self.chain.submit(wallet.sign_transfer(self.alice, self.bob["address"], 1, BASE_FEE, 0))
        with self.assertRaises(ValueError):
            self.chain.submit(wallet.sign_transfer(self.alice, self.bob["address"], 1, BASE_FEE + 1, 1))
        with self.assertRaises(ValueError):
            self.chain.submit(wallet.sign_transfer(self.alice, self.bob["address"], 10 ** 12 * UNIT, BASE_FEE, 1))

    def test_security_validation(self):
        from blockchain.kenan_chain import CHAIN_ID, canonical_message, is_valid_address
        # domain separation + address format
        tx = wallet.sign_transfer(self.alice, self.bob["address"], 1, BASE_FEE, 0)
        self.assertIn(CHAIN_ID, canonical_message(tx).decode())
        self.assertTrue(is_valid_address(self.bob["address"]))
        self.assertFalse(is_valid_address("kenan1short"))
        self.assertFalse(is_valid_address("kenan1" + "g" * 40))
        # malformed input must fail cleanly, never raise KeyError
        self.assertIsNotNone(self.chain.validate({"to": self.bob["address"]}))
        self.assertIsNotNone(self.chain.validate("not-a-dict"))
        malformed_from = dict(tx, **{"from": ["not", "an", "address"]})
        self.assertIsNotNone(self.chain.validate(malformed_from))
        # bad recipient + oversized memo rejected
        bad = wallet.sign_transfer(self.alice, "kenan1short", 1, BASE_FEE, 0)
        self.assertIsNotNone(self.chain.validate(bad))
        long_memo = wallet.sign_transfer(self.alice, self.bob["address"], 1, BASE_FEE, 0, memo="x" * 257)
        self.assertIsNotNone(self.chain.validate(long_memo))
        # tampered linkage must not load silently
        import json as _json
        p = os.path.join(self.tmp.name, "chain.json")
        with open(p, encoding="utf-8") as f:
            data = _json.load(f)
        data["blocks"][0]["hash"] = "0" * 64
        with open(p, "w", encoding="utf-8") as f:
            _json.dump(data, f)
        with self.assertRaises(ValueError):
            Chain(path=p)

    def test_persistence_and_lookup(self):
        tx = wallet.sign_transfer(self.alice, self.bob["address"], 5 * UNIT, BASE_FEE, 0)
        res = self.chain.submit(tx)
        reloaded = Chain(path=os.path.join(self.tmp.name, "chain.json"))
        self.assertEqual(reloaded.balance(self.bob["address"]), 5 * UNIT)
        self.assertEqual(reloaded.balance(self.alice["address"]), 1000 * UNIT - 5 * UNIT - BASE_FEE)
        self.assertEqual(reloaded.nonces[self.alice["address"]], 1)
        self.assertEqual(reloaded.total_supply, self.chain.total_supply)
        self.assertEqual(reloaded.burned, self.chain.burned)
        self.assertIsNotNone(reloaded.get_tx(res["hash"]))
        self.assertIsNone(reloaded.get_tx("0" * 64))
        with self.assertRaises(ValueError):
            reloaded.submit(tx)

    def test_faucet_is_recorded_and_capped_across_restart(self):
        self.chain.faucet(self.bob["address"], 500 * UNIT)
        self.assertEqual(self.chain.blocks[-1]["txs"][0]["kind"], "faucet")
        reloaded = Chain(path=os.path.join(self.tmp.name, "chain.json"))
        self.assertEqual(reloaded.balance(self.bob["address"]), 500 * UNIT)
        with self.assertRaises(ValueError):
            reloaded.faucet(self.bob["address"], 501 * UNIT)

    def test_modified_balance_snapshot_is_rejected(self):
        path = os.path.join(self.tmp.name, "chain.json")
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        data["balances"][self.alice["address"]] += 1
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        with self.assertRaisesRegex(ValueError, "snapshot"):
            Chain(path=path)

    def test_modified_transaction_ledger_is_rejected(self):
        self.chain.submit(wallet.sign_transfer(self.alice, self.bob["address"], UNIT, BASE_FEE, 0))
        path = os.path.join(self.tmp.name, "chain.json")
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        data["blocks"][-1]["txs"][0]["amount"] += 1
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        with self.assertRaisesRegex(ValueError, "transaction hash"):
            Chain(path=path)


class TestExchange(unittest.TestCase):
    def test_limit_match_partial_and_reserved(self):
        e = Engine()
        e.deposit_demo("seller", "KEN", 10)
        e.deposit_demo("buyer", "USD-DEMO", 1000)
        e.place("seller", "sell", "KEN/USD-DEMO", "limit", 10, 10.0)
        o = e.place("buyer", "buy", "KEN/USD-DEMO", "limit", 4, 10.0)
        self.assertEqual(o.status, "filled")
        self.assertEqual(len(e.trades), 1)
        self.assertAlmostEqual(e.trades[0]["qty"], 4)
        self.assertEqual(e.balances["seller"]["KEN"]["reserved"], 6)
        with self.assertRaises(ValueError):
            e.place("buyer", "buy", "KEN/USD-DEMO", "limit", 100000, 10.0)

    def test_cancel_releases_reservation(self):
        e = Engine()
        e.deposit_demo("seller", "KEN", 5)
        o = e.place("seller", "sell", "KEN/USD-DEMO", "limit", 5, 10.0)
        e.cancel(o.id, "seller")
        self.assertEqual(e.balances["seller"]["KEN"]["free"], 5)
        self.assertEqual(e.balances["seller"]["KEN"]["reserved"], 0)

    def test_invalid_order_fields_and_demo_deposit_rejected(self):
        e = Engine()
        e.deposit_demo("seller", "KEN", 5)
        with self.assertRaises(ValueError):
            e.deposit_demo("seller", "KEN", -1)
        with self.assertRaises(ValueError):
            e.place("seller", "sideways", "KEN/USD-DEMO", "limit", 1, 1.0)
        with self.assertRaises(ValueError):
            e.place("seller", "sell", "KEN/USD-DEMO", "stop", 1, 1.0)
        with self.assertRaises(ValueError):
            e.place("seller", "sell", "KEN/USD-DEMO", "limit", float("nan"), 1.0)
        self.assertEqual(e.orders, [])
        self.assertEqual(e.balances["seller"]["KEN"]["free"], 5)

    def test_limit_buy_price_improvement_releases_surplus(self):
        e = Engine()
        e.deposit_demo("seller", "KEN", 5)
        e.deposit_demo("buyer", "USD-DEMO", 100)
        e.place("seller", "sell", "KEN/USD-DEMO", "limit", 5, 10.0)
        order = e.place("buyer", "buy", "KEN/USD-DEMO", "limit", 4, 12.0)
        self.assertEqual(order.status, "filled")
        self.assertEqual(e.balances["buyer"]["KEN"]["free"], 4)
        self.assertEqual(e.balances["buyer"]["USD-DEMO"]["free"], 60)
        self.assertEqual(e.balances["buyer"]["USD-DEMO"]["reserved"], 0)

    def test_partial_limit_buy_cancel_returns_all_remaining_reserve(self):
        e = Engine()
        e.deposit_demo("seller", "KEN", 2)
        e.deposit_demo("buyer", "USD-DEMO", 100)
        e.place("seller", "sell", "KEN/USD-DEMO", "limit", 2, 10.0)
        order = e.place("buyer", "buy", "KEN/USD-DEMO", "limit", 4, 12.0)
        self.assertEqual(order.status, "open")
        e.cancel(order.id, "buyer")
        self.assertEqual(e.balances["buyer"]["USD-DEMO"]["free"], 80)
        self.assertEqual(e.balances["buyer"]["USD-DEMO"]["reserved"], 0)


class TestModules(unittest.TestCase):
    def test_shield_never_claims_safe_without_data(self):
        r = shield_check({"to": "bad", "amount": 1, "fee": 0})
        self.assertEqual(r["level"], "blocked")
        r = shield_check({"to": "kenan1" + "b" * 40, "amount": 100, "fee": 50})
        self.assertEqual(r["level"], "warning")

    def test_intelligence_refuses_keys_and_draft_unsigned(self):
        r = intelligence_answer("ما رسوم؟", {"amount_base": 1000, "fee_base": 10, "private_key": "x"})
        self.assertIn("error", r)
        d = draft_transfer("kenan1" + "c" * 40, 1.5)
        self.assertFalse(d["signed"])

    def test_circle_caps_and_threshold(self):
        c = Circle("fam", "Family", ["a", "b", "c"], threshold=2)
        c.set_cap("a", 10)
        with self.assertRaises(ValueError):
            c.request_payment("a", "x", 99)
        req = c.request_payment("a", "x", 5)
        c.approve(req["id"], "b")
        self.assertEqual(c.requests[req["id"]]["status"], "pending")
        c.approve(req["id"], "c")
        self.assertEqual(c.requests[req["id"]]["status"], "approved")

    def test_pay_receipt_needs_tx_hash(self):
        inv = create_invoice("shop", 2.5)
        self.assertTrue(inv["pay_payload"].startswith("kenan://pay"))
        with self.assertRaises(ValueError):
            receipt_for(inv["invoice_id"], "")
        receipt = receipt_for(inv["invoice_id"], "ab" * 32)
        self.assertEqual(receipt["status"], "pending-verification")
        self.assertFalse(receipt["verified"])

    def test_academy_insights_governance_bridge(self):
        self.assertEqual(academy_account("u")["account_type"], "VIRTUAL-DEMO")
        ins = insights({"KEN": 90, "USD": 10}, 123)
        self.assertIsNone(ins["pnl"])
        self.assertIn("unavailable", ins["pnl_note"])
        g = Governance()
        p = g.propose("raise cap?", "")
        g.vote(p["id"], "v1", 10, True)
        self.assertFalse(p["binding"])
        with self.assertRaises(RuntimeError):
            bridge_transfer()
        rp = RecoveryProposal("kenan1" + "d" * 40, ["g1", "g2"], 2, timelock_seconds=10)
        rp.approve("g1")
        self.assertFalse(rp.executable(now=rp.created_at + 999999) if rp.status != "approved-waiting-timelock" else False)
        rp.approve("g2")
        self.assertFalse(rp.executable(now=rp.created_at + 5))
        self.assertTrue(rp.executable(now=rp.created_at + 11))


class TestHttpIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.chain = Chain(path=os.path.join(cls.tmp.name, "chain.json"))
        cls.node = ThreadingHTTPServer(("127.0.0.1", 0), node_handler(cls.chain))
        threading.Thread(target=cls.node.serve_forever, daemon=True).start()
        cls.node_url = f"http://127.0.0.1:{cls.node.server_address[1]}"
        os.environ["KENAN_NODE_URL"] = cls.node_url
        import backend.app as appmod
        appmod.NODE_URL = cls.node_url
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), backend_handler())
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()
        cls.api_url = f"http://127.0.0.1:{cls.api.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.node.shutdown()
        cls.api.shutdown()
        cls.tmp.cleanup()

    def test_node_rpc_transfer_end_to_end(self):
        alice, bob = wallet.create_wallet(), wallet.create_wallet()
        post(self.node_url + "/faucet", {"address": alice["address"]})
        acct = get(self.node_url + f"/accounts/{alice['address']}")
        tx = wallet.sign_transfer(alice, bob["address"], 10 * UNIT, BASE_FEE, acct["nonce"])
        res = post(self.node_url + "/tx/submit", tx)
        self.assertEqual(res["status"], "confirmed")
        self.assertEqual(get(self.node_url + f"/accounts/{bob['address']}")["balance_ken"], 10)
        info = get(self.node_url + "/chain/info")
        self.assertEqual(info["network"], "devnet-test-only")
        # backend proxies LIVE node balance, never its own copy
        proxied = get(self.api_url + f"/backend/balance/{bob['address']}")
        self.assertEqual(proxied["balance_ken"], 10)
        self.assertEqual(proxied["source"], "live node RPC")

    def test_node_rejects_non_object_json_body(self):
        request = urllib.request.Request(
            self.node_url + "/tx/submit", data=b"[]",
            headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 400)

    def test_faucet_is_denied_when_loopback_mutations_are_disabled(self):
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), node_handler(self.chain, allow_loopback_mutations=False))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        address = wallet.create_wallet()["address"]
        before = self.chain.balance(address)
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/faucet",
            data=json.dumps({"address": address}).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(error.exception.code, 403)
            self.assertEqual(self.chain.balance(address), before)
        finally:
            server.shutdown()
            server.server_close()

    def test_backend_demo_order_and_shield(self):
        post(self.api_url + "/exchange/deposit-demo", {"user": "s", "asset": "KEN", "amount": 5})
        post(self.api_url + "/exchange/deposit-demo", {"user": "b", "asset": "USD-DEMO", "amount": 500})
        post(self.api_url + "/exchange/orders", {"user": "s", "side": "sell", "pair": "KEN/USD-DEMO", "kind": "limit", "qty": 5, "price": 10})
        o = post(self.api_url + "/exchange/orders", {"user": "b", "side": "buy", "pair": "KEN/USD-DEMO", "kind": "limit", "qty": 5, "price": 10})
        self.assertEqual(o["status"], "filled")
        self.assertTrue(o["demo_only"])
        sh = post(self.api_url + "/shield/check", {"to": "bad", "amount": 1, "fee": 0})
        self.assertEqual(sh["level"], "blocked")


if __name__ == "__main__":
    unittest.main()
