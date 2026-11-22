"""Password-authenticated local DEMO exchange accounts and bearer sessions.

Passwords are PBKDF2-HMAC-SHA256 hashes with independent random salts. Session
bearer values are returned once and only their SHA-256 digests are persisted.
This is a loopback test service, not a production identity provider.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import Any

from .ledger import DoubleEntryLedger

PASSWORD_ITERATIONS = 600_000
SESSION_TTL_NS = 8 * 60 * 60 * 1_000_000_000
_MIN_PASSWORD_CHARS = 12
_MAX_PASSWORD_BYTES = 1024
_USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_ROLES = frozenset({"trader", "auditor", "operator"})
_DUMMY_SALT = bytes.fromhex("c55b68f6f9ed79ec6d3f78fe00b88a12")
_DUMMY_HASH = hashlib.pbkdf2_hmac("sha256", b"not-a-user-password", _DUMMY_SALT, PASSWORD_ITERATIONS)


class AuthenticationError(PermissionError):
    """Credentials or bearer session are absent, invalid, revoked, or expired."""


class AccountConflict(ValueError):
    """The requested exchange user identifier is already reserved."""


class AccountPolicyError(ValueError):
    """A username, password, or account role violates the local policy."""


def _password_bytes(password: Any) -> bytes:
    if not isinstance(password, str):
        raise AccountPolicyError("password must be text")
    encoded = password.encode("utf-8")
    if len(password) < _MIN_PASSWORD_CHARS or len(encoded) > _MAX_PASSWORD_BYTES:
        raise AccountPolicyError("password must contain at least 12 characters and be at most 1024 UTF-8 bytes")
    return encoded


def _username(value: Any) -> str:
    if not isinstance(value, str) or not _USER_RE.fullmatch(value):
        raise AccountPolicyError("username must be a 1-64 character local identifier")
    return value


class ExchangeAuth:
    """Small account/session store sharing the exchange's SQLite transaction."""

    def __init__(self, ledger: DoubleEntryLedger):
        if not isinstance(ledger, DoubleEntryLedger):
            raise TypeError("ExchangeAuth requires a DoubleEntryLedger")
        self.ledger = ledger
        self.conn = ledger.conn

    def create_account(self, username: Any, password: Any, *, role: str = "trader") -> dict:
        """Create a local account. HTTP self-registration always fixes role=trader."""
        user = _username(username)
        secret = _password_bytes(password)
        if role not in _ROLES:
            raise AccountPolicyError("unsupported exchange role")
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac("sha256", secret, salt, PASSWORD_ITERATIONS)
        now = time.time_ns()
        try:
            with self.ledger.transaction():
                self.conn.execute(
                    """INSERT INTO exchange_accounts
                       (user, password_salt_hex, password_hash_hex, pbkdf2_iterations,
                        role, status, created_ns)
                       VALUES (?, ?, ?, ?, ?, 'active', ?)""",
                    (user, salt.hex(), password_hash.hex(), PASSWORD_ITERATIONS, role, now),
                )
        except Exception as error:
            if "UNIQUE constraint failed: exchange_accounts.user" in str(error):
                raise AccountConflict("username is already registered or reserved") from None
            raise
        return {"user": user, "role": role, "created_ns": now}

    def register(self, username: Any, password: Any) -> dict:
        """Open a local trader account; public callers cannot choose a role."""
        return self.create_account(username, password, role="trader")

    def authenticate(self, username: Any, password: Any) -> dict:
        """Verify credentials and return one-time plaintext bearer material."""
        valid_username = isinstance(username, str) and _USER_RE.fullmatch(username) is not None
        try:
            secret = _password_bytes(password)
        except AccountPolicyError:
            secret = b"invalid-password-input"
            valid_username = False
        user = username if valid_username else ""
        with self.ledger._lock:
            row = self.conn.execute(
                """SELECT user, password_salt_hex, password_hash_hex, pbkdf2_iterations, role, status
                   FROM exchange_accounts WHERE user = ?""",
                (user,),
            ).fetchone()
        if row and row["status"] == "active" and row["password_salt_hex"] and row["password_hash_hex"]:
            try:
                salt = bytes.fromhex(str(row["password_salt_hex"]))
                iterations = int(row["pbkdf2_iterations"])
                expected = bytes.fromhex(str(row["password_hash_hex"]))
                if len(salt) != 16 or len(expected) != 32 or iterations != PASSWORD_ITERATIONS:
                    raise ValueError("invalid account credential metadata")
                derived = hashlib.pbkdf2_hmac("sha256", secret, salt, iterations)
                valid = hmac.compare_digest(derived, expected)
            except (TypeError, ValueError):
                derived = hashlib.pbkdf2_hmac("sha256", secret, _DUMMY_SALT, PASSWORD_ITERATIONS)
                hmac.compare_digest(derived, _DUMMY_HASH)
                valid = False
        else:
            derived = hashlib.pbkdf2_hmac("sha256", secret, _DUMMY_SALT, PASSWORD_ITERATIONS)
            hmac.compare_digest(derived, _DUMMY_HASH)
            valid = False
        if not valid:
            raise AuthenticationError("invalid username or password")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = time.time_ns()
        expires = now + SESSION_TTL_NS
        with self.ledger.transaction():
            self.conn.execute(
                "INSERT INTO exchange_sessions(token_hash, user, created_ns, expires_ns) VALUES (?, ?, ?, ?)",
                (token_hash, str(row["user"]), now, expires),
            )
        return {
            "token": token,
            "user": str(row["user"]),
            "role": str(row["role"]),
            "expires_ns": expires,
        }

    def principal(self, token: Any) -> dict:
        """Resolve an active bearer token; expired/revoked tokens fail closed."""
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise AuthenticationError("authentication required")
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = time.time_ns()
        with self.ledger._lock:
            row = self.conn.execute(
                """SELECT s.user, s.expires_ns, s.revoked_ns, a.role, a.status
                   FROM exchange_sessions s JOIN exchange_accounts a ON a.user = s.user
                   WHERE s.token_hash = ?""",
                (token_hash,),
            ).fetchone()
        if not row or row["status"] != "active" or row["revoked_ns"] is not None or int(row["expires_ns"]) <= now:
            raise AuthenticationError("authentication required")
        return {"user": str(row["user"]), "role": str(row["role"])}

    def revoke(self, token: Any) -> bool:
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            return False
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        now = time.time_ns()
        with self.ledger.transaction():
            cursor = self.conn.execute(
                "UPDATE exchange_sessions SET revoked_ns = ? WHERE token_hash = ? AND revoked_ns IS NULL",
                (now, token_hash),
            )
            return cursor.rowcount == 1


def bearer_token(header: str | None) -> str | None:
    if not isinstance(header, str):
        return None
    scheme, separator, value = header.partition(" ")
    token = value.strip()
    if not separator or scheme.lower() != "bearer" or not token or any(ch.isspace() for ch in token):
        return None
    return token
