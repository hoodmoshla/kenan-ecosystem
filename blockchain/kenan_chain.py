"""Kenan devnet chain core — reference implementation in Python.

This is a local development chain, not consensus-secure infrastructure. Its
JSON file is a single-node ledger, not a production trust anchor. Balances,
nonces, seen transaction IDs, faucet issuance and supply are reconstructed
from validated block contents on every restart; stored snapshots are checked,
never used as the source of truth.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

DECIMALS = 9
UNIT = 10 ** DECIMALS
BASE_FEE = 1_000_000  # 0.001 KEN
GENESIS_SUPPLY = 100_000_000 * UNIT  # devnet test tokens only
CHAIN_ID = "kenan-devnet-1"
FEE_COLLECTOR = "kenan1" + "00" * 20
MAX_FAUCET_PER_ADDRESS = 1000 * UNIT


def address_from_pubkey(pubkey_bytes: bytes) -> str:
    return "kenan1" + hashlib.sha256(pubkey_bytes).hexdigest()[:40]


def is_valid_address(addr: str) -> bool:
    return (isinstance(addr, str) and len(addr) == 46 and addr.startswith("kenan1")
            and all(c in "0123456789abcdef" for c in addr[6:]))


def canonical_message(tx: dict) -> bytes:
    parts = [
        CHAIN_ID, str(tx["from"]), str(tx["to"]), str(int(tx["amount"])),
        str(int(tx["fee"])), str(int(tx["nonce"])), str(tx.get("memo", "")),
    ]
    return "|".join(parts).encode()


def tx_hash(tx: dict) -> str:
    payload = dict(tx)
    for key in ("signature", "hash", "status", "block_height"):
        payload.pop(key, None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def faucet_event_hash(event: dict) -> str:
    payload = {key: event[key] for key in ("kind", "address", "amount", "height")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def entry_hash(entry: dict) -> str:
    return str(entry.get("hash")) if entry.get("kind") == "faucet" else tx_hash(entry)


def verify_signature(tx: dict) -> bool:
    try:
        pub_bytes = bytes.fromhex(tx["pubkey"])
        Ed25519PublicKey.from_public_bytes(pub_bytes).verify(
            bytes.fromhex(tx["signature"]), canonical_message(tx)
        )
        return address_from_pubkey(pub_bytes) == tx["from"]
    except (ValueError, InvalidSignature, KeyError, TypeError):
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
    faucet_minted: dict = field(default_factory=dict)
    _tx_index: dict = field(default_factory=dict, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self):
        if self.path and os.path.exists(self.path):
            self._load()
        elif not self.blocks:
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
        self.faucet_minted = {}
        self._tx_index = {}
        self.burned = 0
        self.total_supply = GENESIS_SUPPLY
        self._save()

    def _snapshot(self):
        return copy.deepcopy((self.blocks, self.balances, self.nonces, self.seen_tx,
                              self.faucet_minted, self._tx_index, self.burned, self.total_supply))

    def _restore(self, snapshot):
        (self.blocks, self.balances, self.nonces, self.seen_tx, self.faucet_minted,
         self._tx_index, self.burned, self.total_supply) = snapshot

    def _save(self):
        if not self.path:
            return
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.{threading.get_ident()}.tmp"
        data = {
            "schema_version": 2,
            "blocks": self.blocks,
            "balances": self.balances,
            "nonces": self.nonces,
            "seen_tx": sorted(self.seen_tx),
            "faucet_minted": self.faucet_minted,
            "burned": self.burned,
            "total_supply": self.total_supply,
        }
        try:
            with open(tmp, "w", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            try:
                dir_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _load(self):
        with open(self.path, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or not isinstance(data.get("blocks"), list) or not data["blocks"]:
            raise ValueError("chain state has no valid block ledger; refusing to replace it")
        if data.get("schema_version") not in (None, 2):
            raise ValueError("unsupported chain state schema; original file was preserved")

        blocks = data["blocks"]
        genesis = blocks[0]
        if (not isinstance(genesis, dict) or genesis.get("height") != 0
                or genesis.get("prev_hash") != "0" * 64
                or genesis.get("hash") != hashlib.sha256(b"kenan-genesis").hexdigest()
                or genesis.get("producer") != "genesis" or genesis.get("txs") != []
                or genesis.get("total_supply") != GENESIS_SUPPLY or genesis.get("burned") != 0):
            raise ValueError("invalid genesis block; chain state was not loaded")

        self.blocks = [genesis]
        self.balances = {FEE_COLLECTOR: GENESIS_SUPPLY}
        self.nonces = {}
        self.seen_tx = set()
        self.faucet_minted = {}
        self._tx_index = {}
        self.burned = 0
        self.total_supply = GENESIS_SUPPLY
        previous_hash = genesis["hash"]

        for height, block in enumerate(blocks[1:], start=1):
            if not isinstance(block, dict) or block.get("height") != height or block.get("prev_hash") != previous_hash:
                raise ValueError("chain state failed height/linkage check (corrupt or tampered blocks?)")
            txs = block.get("txs")
            tx_hashes = block.get("tx_hashes")
            if not isinstance(txs, list) or not isinstance(tx_hashes, list):
                raise ValueError("block is missing its transaction ledger")
            if type(block.get("timestamp")) is not int or not isinstance(block.get("producer"), str):
                raise ValueError("malformed block header")
            header = {"height": height, "prev_hash": previous_hash,
                      "timestamp": block["timestamp"], "tx_hashes": tx_hashes,
                      "producer": block["producer"]}
            expected_block_hash = hashlib.sha256(
                json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            if block.get("hash") != expected_block_hash:
                raise ValueError("chain state failed block hash check")

            computed_hashes = []
            for position, entry in enumerate(txs):
                if not isinstance(entry, dict):
                    raise ValueError("malformed ledger entry")
                if entry.get("kind") == "faucet":
                    if (entry.get("height") != height or type(entry.get("amount")) is not int
                            or entry["amount"] <= 0 or entry["amount"] > MAX_FAUCET_PER_ADDRESS
                            or not is_valid_address(entry.get("address"))
                            or entry.get("status") != "devnet-mint"
                            or entry.get("hash") != faucet_event_hash(entry)):
                        raise ValueError("invalid or uncommitted faucet event")
                    address, amount = entry["address"], entry["amount"]
                    minted = self.faucet_minted.get(address, 0) + amount
                    if minted > MAX_FAUCET_PER_ADDRESS:
                        raise ValueError("faucet ledger exceeds its per-address devnet cap")
                    self.faucet_minted[address] = minted
                    self.balances[address] = self.balance(address) + amount
                    self.total_supply += amount
                    computed_hashes.append(entry["hash"])
                    continue

                tx = {key: entry[key] for key in ("from", "to", "amount", "fee", "nonce", "memo", "pubkey", "signature") if key in entry}
                if entry.get("status") != "confirmed" or entry.get("hash") != tx_hash(tx):
                    raise ValueError("transaction hash/status does not match block ledger")
                error = self.validate(tx)
                if error:
                    raise ValueError(f"invalid transaction in block {height}: {error}")
                amount, fee = tx["amount"], tx["fee"]
                sender, recipient = tx["from"], tx["to"]
                self.balances[sender] = self.balance(sender) - amount - fee
                self.balances[recipient] = self.balance(recipient) + amount
                burn = fee // 2
                self.burned += burn
                self.total_supply -= burn
                self.balances[FEE_COLLECTOR] = self.balance(FEE_COLLECTOR) + (fee - burn)
                self.nonces[sender] = tx["nonce"] + 1
                txid = tx_hash(tx)
                self.seen_tx.add(txid)
                self._tx_index[txid] = dict(entry, block_height=height)
                computed_hashes.append(txid)

            if tx_hashes != computed_hashes:
                raise ValueError("block transaction hash list does not match its ledger entries")
            if block.get("total_supply") != self.total_supply or block.get("burned") != self.burned:
                raise ValueError("block supply/burn snapshot does not match replayed ledger")
            if sum(self.balances.values()) != self.total_supply:
                raise ValueError("ledger balances do not reconcile to total supply")
            self.blocks.append(block)
            previous_hash = block["hash"]

        try:
            saved_balances = {str(k): int(v) for k, v in data["balances"].items()}
            saved_nonces = {str(k): int(v) for k, v in data["nonces"].items()}
            saved_seen = set(data["seen_tx"])
            saved_minted = {str(k): int(v) for k, v in data.get("faucet_minted", {}).items()}
            saved_burned = int(data["burned"])
            saved_supply = int(data["total_supply"])
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError("saved chain state snapshot is incomplete or malformed") from error
        if (saved_balances != self.balances or saved_nonces != self.nonces
                or saved_seen != self.seen_tx or saved_minted != self.faucet_minted
                or saved_burned != self.burned or saved_supply != self.total_supply):
            raise ValueError("saved account snapshot does not match verified block ledger; state was not trusted")

    # ---- chain ops ----
    def info(self) -> dict:
        with self._lock:
            return {
                "chain_id": CHAIN_ID, "network": "devnet (test tokens only)",
                "height": self.blocks[-1]["height"],
                "latest_hash": self.blocks[-1]["hash"],
                "total_supply_base": self.total_supply,
                "total_supply_ken": self.total_supply / UNIT,
                "burned_base": self.burned,
                "accounts": len(self.balances),
                "tx_count": sum(len(b["txs"]) for b in self.blocks),
                "fee_base": BASE_FEE,
            }

    def balance(self, address: str) -> int:
        with self._lock:
            return int(self.balances.get(address, 0))

    def account_state(self, address: str) -> dict:
        with self._lock:
            return {"address": address, "balance_base": int(self.balances.get(address, 0)),
                    "nonce": int(self.nonces.get(address, 0))}

    def list_blocks(self, limit: int = 20) -> list:
        with self._lock:
            return copy.deepcopy(self.blocks[-limit:] if limit > 0 else self.blocks[:])

    def faucet(self, address: str, amount: int = 100 * UNIT) -> dict:
        """Local devnet mint, recorded as an event and capped at 1000 KEN/address."""
        if not is_valid_address(address):
            raise ValueError("invalid address format")
        if type(amount) is not int or amount <= 0 or amount > MAX_FAUCET_PER_ADDRESS:
            raise ValueError("faucet amount out of range")
        with self._lock:
            if self.faucet_minted.get(address, 0) + amount > MAX_FAUCET_PER_ADDRESS:
                raise ValueError("faucet per-address devnet cap exceeded")
            snapshot = self._snapshot()
            try:
                height = self.blocks[-1]["height"] + 1
                event = {"kind": "faucet", "address": address, "amount": amount,
                         "height": height, "status": "devnet-mint"}
                event["hash"] = faucet_event_hash(event)
                self.faucet_minted[address] = self.faucet_minted.get(address, 0) + amount
                self.balances[address] = self.balance(address) + amount
                self.total_supply += amount
                self._append_block([event])
                self._save()
            except Exception:
                self._restore(snapshot)
                raise
            return {"address": address, "amount_base": amount,
                    "balance_base": self.balance(address), "network": "devnet-test-only",
                    "block_height": self.blocks[-1]["height"]}

    def validate(self, tx: dict) -> str | None:
        """Return an error string instead of raising for malformed submissions."""
        if not isinstance(tx, dict):
            return "malformed transaction"
        for name in ("from", "to", "pubkey", "signature"):
            if not isinstance(tx.get(name), str) or not tx.get(name):
                return f"missing field: {name}"
        if any(type(tx.get(name)) is not int for name in ("amount", "fee", "nonce")):
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
        if tx["to"] == tx["from"]:
            return "sender and recipient must differ"
        if not is_valid_address(tx["to"]):
            return "invalid recipient address"
        if nonce < 0 or self.nonces.get(tx["from"], 0) != nonce:
            return f"bad nonce: expected {self.nonces.get(tx['from'], 0)}"
        if self.balance(tx["from"]) < amount + fee:
            return "insufficient balance (amount + fee)"
        transaction_id = tx_hash(tx)
        if transaction_id in self.seen_tx:
            return "duplicate transaction (replay protection)"
        if not verify_signature(tx):
            return "invalid signature"
        return None

    def submit(self, tx: dict) -> dict:
        with self._lock:
            error = self.validate(tx)
            if error:
                raise ValueError(error)
            snapshot = self._snapshot()
            try:
                amount, fee = tx["amount"], tx["fee"]
                sender, recipient = tx["from"], tx["to"]
                self.balances[sender] = self.balance(sender) - amount - fee
                self.balances[recipient] = self.balance(recipient) + amount
                burn = fee // 2
                self.burned += burn
                self.total_supply -= burn
                self.balances[FEE_COLLECTOR] = self.balance(FEE_COLLECTOR) + (fee - burn)
                self.nonces[sender] = tx["nonce"] + 1
                transaction_id = tx_hash(tx)
                self.seen_tx.add(transaction_id)
                stored = dict(tx, hash=transaction_id, status="confirmed")
                block = self._append_block([stored])
                self._tx_index[transaction_id] = dict(stored, block_height=block["height"])
                self._save()
            except Exception:
                self._restore(snapshot)
                raise
            return {"hash": transaction_id, "block_height": block["height"],
                    "block_hash": block["hash"], "status": "confirmed",
                    "fee_base": fee, "burned_base": burn}

    def mine_empty(self) -> dict:
        with self._lock:
            snapshot = self._snapshot()
            try:
                block = self._append_block([])
                self._save()
                return copy.deepcopy(block)
            except Exception:
                self._restore(snapshot)
                raise

    def _append_block(self, txs: list) -> dict:
        previous = self.blocks[-1]
        hashes = [entry_hash(entry) for entry in txs]
        header = {
            "height": previous["height"] + 1, "prev_hash": previous["hash"],
            "timestamp": int(time.time()), "tx_hashes": hashes,
            "producer": "kenan-devnet-producer",
        }
        raw = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        block = dict(header, txs=txs, hash=hashlib.sha256(raw).hexdigest(),
                     total_supply=self.total_supply, burned=self.burned)
        self.blocks.append(block)
        return block

    def get_tx(self, transaction_id: str) -> dict | None:
        with self._lock:
            result = self._tx_index.get(transaction_id)
            return copy.deepcopy(result) if result is not None else None
