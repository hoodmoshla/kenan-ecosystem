"""Kenan backend API (stdlib only).

Run after the node with: KENAN_NODE_URL=http://127.0.0.1:8545 python3 -m backend.app --port 8000
Admin endpoints require X-Admin-Token matching KENAN_ADMIN_TOKEN. Exchange
routes are loopback-only, test-only, and never create deposits or chain funds.
"""
from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.exchange.auth import (  # noqa: E402
    AccountPolicyError, AuthenticationError, ExchangeAuth, bearer_token,
)
from network_utils import validate_bind_host  # noqa: E402
from backend.exchange.engine import (  # noqa: E402
    Engine, ExchangeError, IdempotencyConflict, InsufficientFunds, OrderNotFound,
)
from backend.modules.features import (  # noqa: E402
    shield_check, intelligence_answer, draft_transfer, Circle,
    create_invoice, receipt_for, academy_account, insights, Governance,
    bridge_transfer)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCHANGE_DB = os.path.join(ROOT, "data", "demo-exchange.sqlite3")
ENGINE = Engine(EXCHANGE_DB)
AUTH = ExchangeAuth(ENGINE.ledger)
CIRCLES: dict = {}
GOV = Governance()
NODE_URL = os.environ.get("KENAN_NODE_URL", "http://127.0.0.1:8545")


def audit(event: str, detail: dict):
    """Legacy JSONL audit sink for non-exchange modules."""
    path = os.path.join(ROOT, "data", "audit.log")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"event": event, "detail": detail}, ensure_ascii=False) + "\n")


def node_get(path: str):
    with urllib.request.urlopen(NODE_URL + path, timeout=5) as r:
        return json.loads(r.read())


def exchange_client_allowed(address: str) -> bool:
    """Exchange endpoints are restricted to local callers, even on LAN binds."""
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    return parsed.is_loopback


def make_handler():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            if urlsplit(self.path).path.startswith("/exchange/") and isinstance(obj, dict):
                obj = {"environment": "DEMO", "demo_only": True, "test_only": True, **obj}
            body = json.dumps(obj, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if urlsplit(self.path).path.startswith("/exchange/"):
                self.send_header("Cache-Control", "no-store")
                self.send_header("Pragma", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}

        def _exchange_principal(self):
            token = bearer_token(self.headers.get("Authorization"))
            try:
                if token is None:
                    raise AuthenticationError("authentication required")
                return AUTH.principal(token)
            except AuthenticationError:
                try:
                    ENGINE.record_rejected_event(
                        "exchange_auth_denied",
                        reference=urlsplit(self.path).path,
                        payload={"reason": "missing_or_invalid_session"},
                    )
                except Exception:
                    pass
                self._send(401, {"error": "authentication required", "test_only": True})
                return None

        def _check_requested_user(self, requested, principal):
            if requested is not None and requested != principal["user"]:
                raise PermissionError("request is not authorized for another account")
            return principal["user"]

        def _exchange_guard(self, path: str) -> bool:
            if not path.startswith("/exchange/") or exchange_client_allowed(self.client_address[0]):
                return True
            try:
                ENGINE.record_rejected_event(
                    "remote_exchange_request_denied",
                    reference=path,
                    payload={"client_ip": self.client_address[0]},
                )
            except Exception:
                pass
            self._send(403, {"error": "exchange API is restricted to loopback", "test_only": True})
            return False

        def _exchange_error(self, error: Exception, path: str, body: dict | None = None, principal: dict | None = None):
            # Never trust a client-supplied user identifier for the audit actor.
            user = principal.get("user", "anonymous") if principal else "anonymous"
            try:
                ENGINE.record_rejected_event(
                    "exchange_request_rejected",
                    user=user,
                    reference=path,
                    payload={"reason": str(error)[:240]},
                )
            except Exception:
                pass
            if isinstance(error, AuthenticationError):
                code = 401
            elif isinstance(error, (InsufficientFunds, IdempotencyConflict)):
                code = 409
            elif isinstance(error, OrderNotFound):
                code = 404
            elif isinstance(error, PermissionError):
                code = 403
            elif isinstance(error, (ExchangeError, KeyError, TypeError, ValueError)):
                code = 400
            else:
                code = 500
            message = str(error) if code < 500 else "exchange operation failed; inspect the local audit record"
            return self._send(code, {"error": message, "test_only": True})

        def do_GET(self):
            parsed = urlsplit(self.path)
            if not self._exchange_guard(parsed.path):
                return
            principal = None
            if parsed.path.startswith("/exchange/") and parsed.path != "/exchange/book":
                principal = self._exchange_principal()
                if principal is None:
                    return
            if parsed.path == "/health":
                return self._send(200, {"ok": True, "service": "kenan-backend", "test_only": True})
            if parsed.path == "/exchange/me":
                return self._send(200, {"user": principal["user"], "role": principal["role"], "test_only": True})
            if parsed.path.startswith("/backend/balance/"):
                addr = parsed.path.split("/")[-1]
                try:
                    return self._send(200, dict(node_get(f"/accounts/{addr}"), source="live node RPC"))
                except Exception as e:
                    return self._send(502, {"error": f"node unreachable: {e}"})
            if parsed.path.startswith("/exchange/balances/"):
                try:
                    user = self._check_requested_user(unquote(parsed.path[len("/exchange/balances/"):]), principal)
                except Exception as e:
                    return self._exchange_error(e, parsed.path, principal=principal)
                try:
                    balances = ENGINE.balances_snapshot(user)
                    return self._send(200, {"user": user, "balances": balances, "test_only": True,
                                            "units": "integer ledger units; not mapped to chain decimals"})
                except Exception as e:
                    return self._exchange_error(e, parsed.path, principal=principal)
            if parsed.path == "/exchange/book":
                pair = parse_qs(parsed.query).get("pair", [""])[0]
                try:
                    if not pair:
                        raise ExchangeError("pair is required")
                    return self._send(200, ENGINE.book(pair))
                except Exception as e:
                    return self._exchange_error(e, parsed.path)
            if parsed.path == "/exchange/trades":
                users = parse_qs(parsed.query).get("user")
                try:
                    user = self._check_requested_user(users[0] if users else None, principal)
                    trades = ENGINE.trades_snapshot(user)
                    return self._send(200, {"trades": trades, "test_only": True})
                except Exception as e:
                    return self._exchange_error(e, parsed.path, principal=principal)
            if parsed.path == "/exchange/orders":
                users = parse_qs(parsed.query).get("user")
                try:
                    user = self._check_requested_user(users[0] if users else None, principal)
                    orders = ENGINE.orders_snapshot(user)
                    return self._send(200, {"orders": orders, "test_only": True})
                except Exception as e:
                    return self._exchange_error(e, parsed.path, principal=principal)
            if parsed.path == "/admin/status":
                token = os.environ.get("KENAN_ADMIN_TOKEN", "")
                provided = self.headers.get("X-Admin-Token", "")
                if not token or not hmac.compare_digest(token, provided):
                    return self._send(503 if not token else 403,
                                      {"error": "admin disabled or unauthorized"})
                counts = ENGINE.counts()
                return self._send(200, {"node_url": NODE_URL, **counts, "circles": len(CIRCLES)})
            if parsed.path.startswith("/exchange/"):
                try:
                    ENGINE.record_rejected_event(
                        "exchange_request_rejected", reference=parsed.path,
                        payload={"reason": "unknown exchange endpoint"},
                    )
                except Exception:
                    pass
                return self._send(404, {"error": "not found", "test_only": True})
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            path = urlsplit(self.path).path
            if not self._exchange_guard(path):
                return
            b = self._body()
            principal = None
            if path.startswith("/exchange/") and path not in (
                "/exchange/register", "/exchange/login",
                "/exchange/deposit-demo", "/exchange/deposit", "/exchange/withdraw",
            ):
                principal = self._exchange_principal()
                if principal is None:
                    return
            try:
                if path == "/exchange/register":
                    if set(b) != {"username", "password"}:
                        raise AccountPolicyError("registration requires username and password only")
                    account = AUTH.register(b.get("username"), b.get("password"))
                    return self._send(201, {"account": account, "test_only": True})
                if path == "/exchange/login":
                    if set(b) != {"username", "password"}:
                        raise AuthenticationError("invalid username or password")
                    session = AUTH.authenticate(b.get("username"), b.get("password"))
                    return self._send(200, {"session": session, "test_only": True})
                if path == "/exchange/logout":
                    token = bearer_token(self.headers.get("Authorization"))
                    AUTH.revoke(token)
                    return self._send(200, {"logged_out": True, "test_only": True})
                if path in ("/exchange/deposit-demo", "/exchange/deposit", "/exchange/withdraw"):
                    try:
                        ENGINE.record_rejected_event(
                            "funding_or_withdrawal_unavailable", user="anonymous", reference=path,
                            payload={"reason": "disabled; no exchange funds were changed"},
                        )
                    finally:
                        return self._send(410, {"error": "exchange deposits and withdrawals are unavailable",
                                                "test_only": True})
                if path == "/exchange/orders":
                    if principal["role"] != "trader":
                        raise PermissionError("this role cannot submit DEMO orders")
                    allowed = {"user", "side", "pair", "kind", "qty_units", "price_num", "price_den", "quote_budget_units"}
                    unexpected = set(b) - allowed
                    if unexpected:
                        raise ExchangeError(f"unsupported order fields: {', '.join(sorted(unexpected))}")
                    user = self._check_requested_user(b.get("user"), principal)
                    result = ENGINE.place(
                        user, b.get("side"), b.get("pair"), b.get("kind", "limit"),
                        b.get("qty_units"), b.get("price_num"), b.get("price_den"),
                        b.get("quote_budget_units"), self.headers.get("Idempotency-Key"),
                    )
                    return self._send(200, result)
                if path.startswith("/exchange/orders/") and path.endswith("/cancel"):
                    parts = path.strip("/").split("/")
                    if len(parts) != 4:
                        raise ExchangeError("invalid cancel route")
                    if principal["role"] != "trader":
                        raise PermissionError("this role cannot cancel DEMO orders")
                    user = self._check_requested_user(b.get("user"), principal)
                    result = ENGINE.cancel(parts[2], user, self.headers.get("Idempotency-Key"))
                    return self._send(200, result)
                if path.startswith("/exchange/"):
                    try:
                        ENGINE.record_rejected_event(
                            "exchange_request_rejected", user=str(b.get("user", "anonymous")),
                            reference=path, payload={"reason": "unknown exchange endpoint"},
                        )
                    except Exception:
                        pass
                    return self._send(404, {"error": "not found", "test_only": True})
                if path == "/shield/check":
                    return self._send(200, shield_check(b))
                if path == "/intelligence/ask":
                    return self._send(200, intelligence_answer(b.get("question", ""), b.get("context", {})))
                if path == "/intelligence/draft":
                    return self._send(200, draft_transfer(b["to"], float(b["amount_ken"])))
                if path == "/pay/invoice":
                    return self._send(200, create_invoice(b["merchant"], float(b["amount_ken"]), b.get("note", "")))
                if path == "/pay/receipt":
                    return self._send(200, receipt_for(b["invoice_id"], b["tx_hash"]))
                if path == "/circles":
                    c = Circle(b["id"], b["name"], b["members"], b.get("threshold", 2))
                    CIRCLES[c.id] = c
                    return self._send(200, {"id": c.id, "members": sorted(c.members)})
                if path.startswith("/circles/") and path.endswith("/request"):
                    cid = path.split("/")[2]
                    r = CIRCLES[cid].request_payment(b["requester"], b["to"], float(b["amount_ken"]), b.get("note", ""))
                    return self._send(200, {k: (sorted(v) if isinstance(v, set) else v) for k, v in r.items()})
                if path == "/academy/account":
                    return self._send(200, academy_account(b["user"]))
                if path == "/insights":
                    return self._send(200, insights(b.get("holdings", {}), int(b.get("fees_base", 0))))
                if path == "/governance/propose":
                    return self._send(200, GOV.propose(b["title"], b.get("description", "")))
                if path == "/bridge/transfer":
                    bridge_transfer()
            except Exception as e:
                if path.startswith("/exchange/"):
                    return self._exchange_error(e, path, principal=principal)
                code = 503 if "BRIDGE DISABLED" in str(e) else 400
                return self._send(code, {"error": str(e)})
            return self._send(404, {"error": "not found"})
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
