"""Kenan devnet node HTTP RPC (stdlib only).

Run:  python3 -m blockchain.node_server --port 8545 --data data/chain.json
Endpoints:
  GET  /health            GET  /chain/info     GET  /blocks?limit=N
  GET  /blocks/<height>   GET  /accounts/<addr> GET /tx/<hash>
  POST /tx/submit  {signed tx}   POST /faucet {address} (devnet only)
  POST /mine  (empty block, for explorer/tests)   GET /explorer (HTML page)
Every response carries network="devnet-test-only" so a UI can never mistake
test balances for real money.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blockchain.kenan_chain import Chain, BASE_FEE, UNIT  # noqa: E402

EXPLORER_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kenan Explorer — شبكة تجريبية</title>
<style>body{font-family:system-ui,sans-serif;background:#0b2a3a;color:#f5f0e6;max-width:900px;margin:auto;padding:16px}
.card{background:#12394d;border-radius:12px;padding:16px;margin:12px 0}.warn{color:#f2c14e}
pre{white-space:pre-wrap;word-break:break-all}</style></head><body>
<h1>🔍 Kenan Explorer</h1><p class="warn">شبكة تجريبية — أرصدة اختبارية فقط، وليست أموالاً حقيقية.</p>
<div class="card" id="info">جارٍ التحميل…</div>
<div class="card"><h2>أحدث الكتل</h2><pre id="blocks"></pre></div>
<script>async function load(){const i=await (await fetch('/chain/info')).json();
document.getElementById('info').innerHTML=`الشبكة: ${i.chain_id} | الارتفاع: ${i.height} | المعروض: ${i.total_supply_ken} KEN (اختباري) | المحروق: ${i.burned_base} وحدة | الحسابات: ${i.accounts}`;
const b=await (await fetch('/blocks?limit=10')).json();
document.getElementById('blocks').textContent=JSON.stringify(b,null,2);
document.title+='';}load();setInterval(load,5000);</script></body></html>"""


def make_handler(chain: Chain):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep logs clean; never log tx secrets
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = urlparse(self.path)
            q = parse_qs(p.query)
            if p.path == "/health":
                return self._send(200, {"ok": True, "network": "devnet-test-only"})
            if p.path == "/chain/info":
                return self._send(200, dict(chain.info(), network="devnet-test-only"))
            if p.path == "/blocks":
                try:
                    limit = min(int(q.get("limit", ["20"])[0]), 100)
                except (ValueError, TypeError):
                    return self._send(400, {"error": "bad limit"})
                return self._send(200, chain.blocks[-limit:])
            if p.path.startswith("/blocks/"):
                try:
                    h = int(p.path.split("/")[2])
                except (IndexError, ValueError):
                    return self._send(400, {"error": "bad height"})
                for b in chain.blocks:
                    if b["height"] == h:
                        return self._send(200, b)
                return self._send(404, {"error": "block not found"})
            if p.path.startswith("/accounts/"):
                addr = p.path.split("/")[2]
                return self._send(200, {"address": addr,
                                        "balance_base": chain.balance(addr),
                                        "balance_ken": chain.balance(addr) / UNIT,
                                        "nonce": chain.nonces.get(addr, 0),
                                        "network": "devnet-test-only"})
            if p.path.startswith("/tx/"):
                tx = chain.get_tx(p.path.split("/")[2])
                return self._send(200, tx) if tx else self._send(404, {"error": "tx not found"})
            if p.path == "/explorer":
                body = EXPLORER_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if p.path == "/":
                return self._send(200, {"name": "Kenan devnet node",
                                        "network": "devnet-test-only",
                                        "endpoints": ["/health", "/chain/info", "/blocks",
                                                      "/accounts/<addr>", "/tx/<hash>",
                                                      "/tx/submit", "/faucet", "/explorer"]})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._send(400, {"error": "invalid json"})
            if self.path == "/tx/submit":
                try:
                    return self._send(200, chain.submit(body))
                except (ValueError, KeyError, TypeError) as e:
                    return self._send(400, {"error": str(e)})
            if self.path == "/faucet":
                try:
                    return self._send(200, chain.faucet(body.get("address", "")))
                except (ValueError, KeyError, TypeError) as e:
                    return self._send(400, {"error": str(e)})
            if self.path == "/mine":
                return self._send(200, chain.mine_empty())
            return self._send(404, {"error": "not found"})
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8545)
    ap.add_argument("--data", default="data/chain.json")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.data) or ".", exist_ok=True)
    chain = Chain(path=args.data)
    print(f"Kenan devnet node on http://{args.host}:{args.port} "
          f"(fee={BASE_FEE} base, data={args.data})", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(chain)).serve_forever()


if __name__ == "__main__":
    main()
