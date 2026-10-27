"""Kenan backend API (stdlib only) — demo exchange + appendix modules.

Run (after the node):  KENAN_NODE_URL=http://127.0.0.1:8545 python3 -m backend.app --port 8000
Admin endpoints require header X-Admin-Token matching env KENAN_ADMIN_TOKEN;
if the env var is unset, admin endpoints return 503 (disabled), never open.
Audit log: append-only JSON-lines file data/audit.log (operator tooling
should hash-chain it externally; see docs/SECURITY.md).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from network_utils import validate_bind_host  # noqa: E402
from backend.exchange.engine import Engine  # noqa: E402
from backend.modules.features import (  # noqa: E402
    shield_check, intelligence_answer, draft_transfer, Circle,
    create_invoice, receipt_for, academy_account, insights, Governance,
    bridge_transfer)

ENGINE = Engine()
CIRCLES: dict = {}
GOV = Governance()
NODE_URL = os.environ.get("KENAN_NODE_URL", "http://127.0.0.1:8545")


def audit(event: str, detail: dict):
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "audit.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": event, "detail": detail}, ensure_ascii=False) + "\n")


def node_get(path: str):
    with urllib.request.urlopen(NODE_URL + path, timeout=5) as r:
        return json.loads(r.read())


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True, "service": "kenan-backend", "demo_only": True})
            if self.path.startswith("/backend/balance/"):
                addr = self.path.split("/")[-1]
                try:
                    return self._send(200, dict(node_get(f"/accounts/{addr}"), source="live node RPC"))
                except Exception as e:
                    return self._send(502, {"error": f"node unreachable: {e}"})
            parsed = urlsplit(self.path)
            if parsed.path.startswith("/exchange/balances/"):
                user = unquote(parsed.path[len("/exchange/balances/"):])
                if not user or "/" in user:
                    return self._send(400, {"error": "Invalid demo exchange request", "demo_only": True})
                return self._send(200, {"user": user, "balances": ENGINE.balances_snapshot(user), "demo_only": True})
            if parsed.path == "/exchange/book":
                pair = parse_qs(parsed.query).get("pair", [""])[0]
                if not pair:
                    return self._send(400, {"error": "Invalid demo exchange request", "demo_only": True})
                return self._send(200, ENGINE.book(pair))
            if parsed.path == "/exchange/trades":
                return self._send(200, {"trades": ENGINE.trades_snapshot(), "demo_only": True})
            if parsed.path == "/exchange/orders":
                users = parse_qs(parsed.query).get("user")
                orders = ENGINE.orders_snapshot(users[0] if users else None)
                return self._send(200, {"orders": orders, "demo_only": True})
            if self.path == "/admin/status":
                token = os.environ.get("KENAN_ADMIN_TOKEN", "")
                if not token or self.headers.get("X-Admin-Token") != token:
                    return self._send(503 if not token else 403,
                                      {"error": "admin disabled or unauthorized"})
                return self._send(200, {"node_url": NODE_URL, "open_orders": len([o for o in ENGINE.orders if o.status == "open"]),
                                        "trades": len(ENGINE.trades), "circles": len(CIRCLES)})
            if parsed.path.startswith("/exchange/"):
                return self._send(404, {"error": "not found", "demo_only": True})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            b = self._body()
            try:
                if self.path == "/exchange/deposit-demo":
                    ENGINE.deposit_demo(b["user"], b["asset"], float(b["amount"]))
                    audit("demo_deposit", b)
                    return self._send(200, {"user": b["user"], "balances": ENGINE.balances_snapshot(b["user"]), "demo_only": True})
                if self.path == "/exchange/orders":
                    o = ENGINE.place(b["user"], b["side"], b["pair"], b.get("kind", "limit"),
                                     float(b["qty"]), b.get("price"))
                    audit("order", {"id": o.id, "user": o.user, "status": o.status})
                    return self._send(200, {"id": o.id, "status": o.status, "filled": o.filled, "demo_only": True})
                if self.path.startswith("/exchange/orders/") and self.path.endswith("/cancel"):
                    oid = int(self.path.split("/")[3])
                    o = ENGINE.cancel(oid, b["user"])
                    return self._send(200, {"id": o.id, "status": o.status, "demo_only": True})
                if self.path == "/shield/check":
                    return self._send(200, shield_check(b))
                if self.path == "/intelligence/ask":
                    return self._send(200, intelligence_answer(b.get("question", ""), b.get("context", {})))
                if self.path == "/intelligence/draft":
                    return self._send(200, draft_transfer(b["to"], float(b["amount_ken"])))
                if self.path == "/pay/invoice":
                    return self._send(200, create_invoice(b["merchant"], float(b["amount_ken"]), b.get("note", "")))
                if self.path == "/pay/receipt":
                    return self._send(200, receipt_for(b["invoice_id"], b["tx_hash"]))
                if self.path == "/circles":
                    c = Circle(b["id"], b["name"], b["members"], b.get("threshold", 2))
                    CIRCLES[c.id] = c
                    return self._send(200, {"id": c.id, "members": sorted(c.members)})
                if self.path.startswith("/circles/") and self.path.endswith("/request"):
                    cid = self.path.split("/")[2]
                    r = CIRCLES[cid].request_payment(b["requester"], b["to"], float(b["amount_ken"]), b.get("note", ""))
                    return self._send(200, {k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()})
                if self.path == "/academy/account":
                    return self._send(200, academy_account(b["user"]))
                if self.path == "/insights":
                    return self._send(200, insights(b.get("holdings", {}), int(b.get("fees_base", 0))))
                if self.path == "/governance/propose":
                    return self._send(200, GOV.propose(b["title"], b.get("description", "")))
                if self.path == "/bridge/transfer":
                    bridge_transfer()
            except (ValueError, KeyError, RuntimeError, TypeError, OverflowError) as e:
                if self.path.startswith("/exchange/"):
                    return self._send(400, {"error": "Invalid demo exchange request", "demo_only": True})
                code = 503 if "BRIDGE DISABLED" in str(e) else 400
                return self._send(code, {"error": str(e)})
            return self._send(404, {"error": "not found", **({"demo_only": True} if self.path.startswith("/exchange/") else {})})
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    try:
        validate_bind_host(args.host)
    except ValueError as error:
        ap.error(f"invalid --host {args.host!r}: {error}")
    print(f"Kenan backend on http://{args.host}:{args.port} (node={NODE_URL})", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler()).serve_forever()


if __name__ == "__main__":
    main()
