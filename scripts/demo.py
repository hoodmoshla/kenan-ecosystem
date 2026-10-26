"""End-to-end devnet demo (TEST wallets only, generated in-process).

Starts a node on an ephemeral port, faucets test KEN to Alice, sends a
signed transfer to Bob, and prints the verified result + explorer URL.
Run from the repo root:  python3 scripts/demo.py
"""
import importlib.util
import json
import os
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from blockchain.kenan_chain import Chain, UNIT, BASE_FEE  # noqa: E402
from blockchain.node_server import make_handler  # noqa: E402

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


def main():
    import tempfile
    tmp = tempfile.TemporaryDirectory()
    chain = Chain(path=os.path.join(tmp.name, "chain.json"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(chain))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    alice, bob = wallet.create_wallet(), wallet.create_wallet()
    print("Alice:", alice["address"])
    print("Bob:  ", bob["address"])
    post(base + "/faucet", {"address": alice["address"]})
    acct = get(base + f"/accounts/{alice['address']}")
    tx = wallet.sign_transfer(alice, bob["address"], 25 * UNIT, BASE_FEE, acct["nonce"])
    res = post(base + "/tx/submit", tx)
    print("Transfer:", res)
    print("Bob balance (KEN):", get(base + f"/accounts/{bob['address']}")["balance_ken"])
    print("Chain info:", get(base + "/chain/info"))
    print(f"Explorer (while this process runs): {base}/explorer")
    print("NOTE: test wallets/tokens only — nothing here is real money.")
    srv.shutdown()


if __name__ == "__main__":
    main()
