"""Kenan devnet chain core — reference implementation in Python.

Honest scope (see docs/STATUS.md):
- This is a REAL, testable local chain: Ed25519-signed transfers, sequential
  nonces, fees, blocks with hash-chaining, persistent JSON state, replay
  protection. It is NOT a Polkadot SDK / Substrate node: building one needs
  a Rust toolchain, several GB of RAM/disk and hours in this VM, none of
  which are available here (see docs/STATUS.md). The module boundaries
  (sign / balance / submit / watch) are the same ones the Android app and
  the backend use, so a Substrate node can replace this core later behind
  the same RPC shapes without changing the wallet UX.
- All balances here are DEVNET TEST tokens. They are never real money and
  must never be presented as mainnet KEN.

Crypto: Ed25519 via the audited `cryptography` library — never home-made
crypto. Address = "kenan1" + first 20 bytes of SHA-256(public key), hex.
1 KEN = 10**9 base units. Flat fee = 1_000_000 base units (0.001 KEN):
50% burned, 50% credited to the fee-collector account recorded in the block.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature

DECIMALS = 9
UNIT = 10 ** DECIMALS
BASE_FEE = 1_000_000  # 0.001 KEN
GENESIS_SUPPLY = 100_000_000 * UNIT  # devnet test tokens only, see docs
CHAIN_ID = "kenan-devnet-1"
FEE_COLLECTOR = "kenan1" + "00" * 20  # pseudo-account, documented in docs


def address_from_pubkey(pubkey_bytes: bytes) -> str:
    return "kenan1" + hashlib.sha256(pubkey_bytes).hexdigest()[:40]


def is_valid_address(addr: str) -> bool:
    """kenan1 + 40 lowercase hex chars. Rejects truncated/padded addresses
    that would otherwise receive (and strand) funds."""
    if not isinstance(addr, str) or len(addr) != 46 or not addr.startswith("kenan1"):
        return False
    return all(c in "0123456789abcdef" for c in addr[6:])


def canonical_message(tx: dict) -> bytes:
    # Domain separation: CHAIN_ID is signed, so a devnet signature cannot
    # be replayed verbatim on another chain using the same tx format.
    parts = [
        CHAIN_ID, str(tx["from"]), str(tx["to"]), str(int(tx["amount"])),
        str(int(tx["fee"])), str(int(tx["nonce"])), str(tx.get("memo", "")),
    ]
    return "|".join(parts).encode()


def tx_hash(tx: dict) -> str:
    payload = dict(tx)
    payload.pop("signature", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify_signature(tx: dict) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(tx["pubkey"]))
        pub.verify(bytes.fromhex(tx["signature"]), canonical_message(tx))
        return address_from_pubkey(bytes.fromhex(tx["pubkey"])) == tx["from"]
    except (ValueError, InvalidSignature, KeyError):
        return False


@dataclass
class Chain:
    path: str | None = None
    blocks: list = field(default_factory=list)
    balances: dict = field(default_factory=dict)
    nonces: dict = field(default_factory=dict)
    seen_tx: set = field(default_factory=set)
    burned: int = 0
    total_supply: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        if self.path and os.path.exists(self.path):
            self._load()
        if not self.blocks:
            self._genesis()

    # ---- persistence ----
    def _genesis(self):
        genesis = {
            "height": 0, "prev_hash": "0" * 64, "timestamp": int(time.time()),
            "txs": [], "producer": "genesis",
            "hash": hashlib.sha256(b"kenan-genesis").hexdigest(),
            "total_supply": GENESIS_SUPPLY, "burned": 0,
        }
        self.blocks = [genesis]
        self.balances = {FEE_COLLECTOR: GENESIS_SUPPLY}
        self.nonces = {}
        self.seen_tx = set()
        self.burned = 0
        self.total_supply = GENESIS_SUPPLY
        self._save()

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        data = {
            "blocks": self.blocks, "balances": self.balances,
            "nonces": self.nonces, "seen_tx": sorted(self.seen_tx),
            "burned": self.burned, "total_supply": self.total_supply,
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
        os.replace(tmp, self.path)

    def _load(self):
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        # Check block linkage and header hashes only. Account-state snapshots
        # are not rebuilt from a complete ledger; this remains devnet-only.
        prev_hash = "0" * 64
        for i, b in enumerate(data["blocks"]):
            if b["height"] != i or b["prev_hash"] != prev_hash:
                raise ValueError("chain state failed linkage check (tampered blocks?)")
            if i == 0:
                expected = hashlib.sha256(b"kenan-genesis").hexdigest()
            else:
                header = {"height": b["height"], "prev_hash": b["prev_hash"],
                          "timestamp": b["timestamp"], "tx_hashes": b["tx_hashes"],
                          "producer": b["producer"]}
                expected = hashlib.sha256(json.dumps(header, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if b["hash"] != expected:
                raise ValueError("chain state failed hash check (tampered blocks?)")
            prev_hash = b["hash"]
        self.blocks = data["blocks"]
        self.balances = {k: int(v) for k, v in data["balances"].items()}
        self.nonces = {k: int(v) for k, v in data["nonces"].items()}
        self.seen_tx = set(data["seen_tx"])
        self.burned = int(data["burned"])
        self.total_supply = int(data["total_supply"])

    # ---- chain ops ----
    def info(self) -> dict:
        return {
            "chain_id": CHAIN_ID, "network": "devnet (test tokens only)",
            "height": self.blocks[-1]["height"],
            "latest_hash": self.blocks[-1]["hash"],
            "total_supply_base": self.total_supply,
            "total_supply_ken": self.total_supply / UNIT,
            "burned_base": self.burned,
            "accounts": len(self.balances),
            "tx_count": sum(len(b["txs"]) for b in self.blocks),
        }

    def balance(self, address: str) -> int:
        return int(self.balances.get(address, 0))

    def faucet(self, address: str, amount: int = 100 * UNIT) -> dict:
        """Devnet-only test-token faucet. Never enable on a real network."""
        if not is_valid_address(address):
            raise ValueError("invalid address format")
        if amount <= 0 or amount > 1000 * UNIT:
            raise ValueError("faucet amount out of range")
        with self._lock:
            self.balances[address] = self.balance(address) + amount
            self.total_supply += amount
            self._save()
        return {"address": address, "amount_base": amount,
                "balance_base": self.balance(address), "network": "devnet-test-only"}

    def validate(self, tx: dict) -> str | None:
        """Return an error string, or None if the tx is valid.
        Never raises on malformed input: the RPC layer maps any error
        string to HTTP 400 instead of a 500 / dropped connection."""
        if not isinstance(tx, dict):
            return "malformed transaction"
        for field in ("from", "to", "pubkey", "signature"):
            if not isinstance(tx.get(field), str) or not tx.get(field):
                return f"missing field: {field}"
        if any(type(tx.get(field)) is not int for field in ("amount", "fee", "nonce")):
            return "malformed amount/fee/nonce"
        amount, fee, nonce = tx["amount"], tx["fee"], tx["nonce"]
        memo = tx.get("memo", "")
        if not isinstance(memo, str):
            return "memo must be a string"
        if len(memo) > 256:
            return "memo too long (max 256 chars)"
        if amount <= 0:
            return "amount must be positive"
        if fee != BASE_FEE:
            return f"fee must equal fixed devnet fee {BASE_FEE} base units"
        if not is_valid_address(tx["from"]):
            return "invalid sender address"
        if tx.get("to", "") == tx.get("from", ""):
            return "sender and recipient must differ"
        if not is_valid_address(str(tx.get("to", ""))):
            return "invalid recipient address"
        if self.nonces.get(tx["from"], 0) != nonce:
            return f"bad nonce: expected {self.nonces.get(tx['from'], 0)}"
        if self.balance(tx["from"]) < amount + fee:
            return "insufficient balance (amount + fee)"
        h = tx_hash(tx)
        if h in self.seen_tx:
            return "duplicate transaction (replay protection)"
        if not verify_signature(tx):
            return "invalid signature"
        return None

    def submit(self, tx: dict) -> dict:
        with self._lock:
            err = self.validate(tx)
            if err:
                raise ValueError(err)
            amount, fee = int(tx["amount"]), int(tx["fee"])
            sender, recipient = tx["from"], tx["to"]
            self.balances[sender] = self.balance(sender) - amount - fee
            self.balances[recipient] = self.balance(recipient) + amount
            burn, to_collector = fee // 2, fee - fee // 2
            self.burned += burn
            self.total_supply -= burn
            self.balances[FEE_COLLECTOR] = self.balance(FEE_COLLECTOR) + to_collector
            self.nonces[sender] = int(tx["nonce"]) + 1
            h = tx_hash(tx)
            self.seen_tx.add(h)
            stored = dict(tx, hash=h, status="confirmed")
            block = self._append_block([stored])
            self._save()
            return {"hash": h, "block_height": block["height"],
                    "block_hash": block["hash"], "status": "confirmed",
                    "fee_base": fee, "burned_base": burn}

    def mine_empty(self) -> dict:
        with self._lock:
            block = self._append_block([])
            self._save()
            return block

    def _append_block(self, txs: list) -> dict:
        prev = self.blocks[-1]
        header = {
            "height": prev["height"] + 1, "prev_hash": prev["hash"],
            "timestamp": int(time.time()),
            "tx_hashes": [t.get("hash", tx_hash(t)) for t in txs],
            "producer": "kenan-devnet-producer",
        }
        raw = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        block = dict(header, txs=txs, hash=hashlib.sha256(raw).hexdigest(),
                     total_supply=self.total_supply, burned=self.burned)
        self.blocks.append(block)
        return block

    def get_tx(self, h: str) -> dict | None:
        for b in self.blocks:
            for t in b["txs"]:
                if t.get("hash") == h:
                    return dict(t, block_height=b["height"])
        return None
