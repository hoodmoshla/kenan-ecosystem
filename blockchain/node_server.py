"""Kenan devnet node HTTP RPC (stdlib only).

Run: python3 -m blockchain.node_server --port 8545 --data data/chain.json
Mutation endpoints are intended for a local devnet. Faucet and empty-block
mining are loopback-only by default; a non-loopback operator may authorize
them with KENAN_FAUCET_TOKEN. Signed transfers remain signature/nonce checked.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from network_utils import validate_bind_host  # noqa: E402
from blockchain.kenan_chain import Chain, BASE_FEE, UNIT, is_valid_address  # noqa: E402

MAX_BODY_BYTES = 64 * 1024
EXPLORER_HTML = """<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kenan Explorer — شبكة تجريبية</title>
<style>body{font-family:system-ui,sans-serif;background:#0b2a3a;color:#f5f0e6;max-width:900px;margin:auto;padding:16px}
.card{background:#12394d;border-radius:12px;padding:16px;margin:12px 0}.warn{color:#f2c14e}
pre{white-space:pre-wrap;word-break:break-all}</style></head><body>
<h1>Kenan Explorer</h1><p class="warn">شبكة تجريبية — أرصدة اختبارية فقط، وليست أموالاً حقيقية.</p>
<div class="card" id="info">جارٍ التحميل…</div>
<div class="card"><h2>أحدث الكتل</h2><pre id="blocks"></pre></div>
<script>async function load(){const i=await (await fetch('/chain/info')).json();
document.getElementById('info').textContent=`الشبكة: ${i.chain_id} | الارتفاع: ${i.height} | المعروض: ${i.total_supply_ken} KEN (اختباري) | المحروق: ${i.burned_base} وحدة | الحسابات: ${i.accounts}`;
const b=await (await fetch('/blocks?limit=10')).json();
document.getElementById('blocks').textContent=JSON.stringify(b,null,2);
}load();setInterval(load,5000);</script></body></html>"""


def make_handler(chain: Chain, faucet_token: str | None = None, allow_loopback_mutations: bool = True):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # Never log transaction bodies or credentials.
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized_local_mutation(self) -> bool:
            try:
                loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                loopback = False
            if loopback and allow_loopback_mutations:
                return True
            supplied = self.headers.get("X-Faucet-Token", "")
            return bool(faucet_token and hmac.compare_digest(supplied, faucet_token))

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/health":
                return self._send(200, {"ok": True, "network": "devnet-test-only"})
            if parsed.path == "/chain/info":
                return self._send(200, dict(chain.info(), network="devnet-test-only"))
            if parsed.path == "/blocks":
                try:
                    limit = int(query.get("limit", ["20"])[0])
                    if not 1 <= limit <= 100:
                        raise ValueError
                except (ValueError, TypeError):
                    return self._send(400, {"error": "limit must be 1..100"})
                return self._send(200, chain.list_blocks(limit))
            if parsed.path.startswith("/blocks/"):
                try:
                    height = int(parsed.path.split("/")[2])
                except (IndexError, ValueError):
                    return self._send(400, {"error": "bad height"})
                block = next((b for b in chain.list_blocks(1000000) if b["height"] == height), None)
                return self._send(200, block) if block else self._send(404, {"error": "block not found"})
            if parsed.path.startswith("/accounts/"):
                address = parsed.path.split("/", 2)[2]
                if not is_valid_address(address):
                    return self._send(400, {"error": "invalid address"})
                account = chain.account_state(address)
                account["balance_ken"] = account["balance_base"] / UNIT
                account["network"] = "devnet-test-only"
                return self._send(200, account)
            if parsed.path.startswith("/tx/"):
                transaction_id = parsed.path.split("/", 2)[2]
                if not __import__("re").fullmatch(r"[0-9a-f]{64}", transaction_id):
                    return self._send(400, {"error": "invalid transaction hash"})
                tx = chain.get_tx(transaction_id)
                return self._send(200, tx) if tx else self._send(404, {"error": "tx not found"})
            if parsed.path == "/explorer":
                body = EXPLORER_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(body)
            if parsed.path == "/":
                return self._send(200, {"name": "Kenan devnet node", "network": "devnet-test-only",
                                        "endpoints": ["/health", "/chain/info", "/blocks",
                                                      "/accounts/<addr>", "/tx/<hash>",
                                                      "/tx/submit", "/faucet", "/explorer"]})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400, {"error": "invalid content length"})
            if length < 0:
                return self._send(400, {"error": "invalid content length"})
            if length > MAX_BODY_BYTES:
                return self._send(413, {"error": "request body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._send(400, {"error": "invalid json"})
            if not isinstance(body, dict):
                return self._send(400, {"error": "JSON body must be an object"})
            path = urlparse(self.path).path
            if path == "/tx/submit":
                try:
                    return self._send(200, chain.submit(body))
                except (ValueError, KeyError, TypeError) as error:
                    return self._send(400, {"error": str(error)})
            if path == "/faucet":
                if not self._authorized_local_mutation():
                    return self._send(403, {"error": "faucet is loopback-only; configure operator authorization for a private devnet"})
                try:
                    amount = body.get("amount_base", 100 * UNIT)
                    if type(amount) is not int:
                        raise ValueError("amount_base must be an integer")
                    return self._send(200, chain.faucet(body.get("address", ""), amount))
                except (ValueError, KeyError, TypeError) as error:
                    return self._send(400, {"error": str(error)})
            if path == "/mine":
                if not self._authorized_local_mutation():
                    return self._send(403, {"error": "block mining is loopback-only"})
                return self._send(200, chain.mine_empty())
            return self._send(404, {"error": "not found"})
    return H


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8545)
    parser.add_argument("--data", default="data/chain.json")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    try:
        validate_bind_host(args.host)
    except ValueError as error:
        parser.error(f"invalid --host {args.host!r}: {error}")
    os.makedirs(os.path.dirname(args.data) or ".", exist_ok=True)
    chain = Chain(path=args.data)
    try:
        local_bind = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        local_bind = args.host.lower() == "localhost"
    faucet_token = os.environ.get("KENAN_FAUCET_TOKEN")
    if not local_bind and not faucet_token:
        print("WARNING: non-loopback bind; faucet and /mine remain disabled without KENAN_FAUCET_TOKEN", file=sys.stderr)
    print(f"Kenan devnet node on http://{args.host}:{args.port} "
          f"(fee={BASE_FEE} base, data={args.data})", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(
        chain, faucet_token=faucet_token, allow_loopback_mutations=local_bind
    )).serve_forever()


if __name__ == "__main__":
    main()
