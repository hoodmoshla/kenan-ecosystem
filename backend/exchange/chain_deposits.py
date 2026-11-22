"""Fail-closed observer for finalized Substrate transfers; never credits the ledger.

The caller must provide data decoded from one finalized block by a trusted local
RPC adapter. This layer independently checks pinned network/runtime identity,
extrinsic hash and call/event agreement, beneficiary binding, and base-unit amount.
It stores only an idempotent review record and checkpoint; it has no ledger-credit
operation and is not a production deposit service.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import time
from typing import Any

from .ledger import DoubleEntryLedger

_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_ACCOUNT_ID_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_U128_MAX = (1 << 128) - 1
_ALLOWED_TRANSFER_CALLS = frozenset({"transfer", "transferAllowDeath", "transferKeepAlive"})


class ChainObservationError(ValueError):
    """A chain observation violates the configured test-only policy."""


@dataclass(frozen=True)
class FinalizedTransferEvent:
    tx_hash: str
    extrinsic_hex: str
    extrinsic_index: int
    phase_extrinsic_index: int
    event_index: int
    pallet: str
    event_name: str
    asset_id: str
    system_success_phase_extrinsic_index: int | None
    sender: str
    beneficiary: str
    amount_base_units: int | str
    call_pallet: str
    call_name: str
    call_sender: str
    call_beneficiary: str
    call_amount_base_units: int | str
    extrinsic_success: bool


@dataclass(frozen=True)
class FinalizedBlock:
    genesis_hash: str
    height: int
    block_hash: str
    parent_hash: str
    finalized: bool
    chain_name: str
    spec_name: str
    spec_version: int
    token_symbol: str
    token_decimals: int
    transfers: tuple[FinalizedTransferEvent, ...]


class ChainDepositObserver:
    """Persist verified finalized transfer observations, explicitly uncredited."""

    def __init__(
        self,
        ledger: DoubleEntryLedger,
        *,
        expected_genesis_hash: str,
        expected_chain_name: str,
        expected_spec_name: str,
        expected_spec_version: int,
        expected_token_symbol: str,
        expected_token_decimals: int,
        expected_asset_id: str,
        watcher_id: str = "local-polkadot-observer-v1",
    ):
        if not isinstance(ledger, DoubleEntryLedger):
            raise TypeError("ChainDepositObserver requires a DoubleEntryLedger")
        self.ledger = ledger
        self.conn = ledger.conn
        self.expected_genesis_hash = self._hash(expected_genesis_hash, "expected genesis hash")
        if not isinstance(expected_chain_name, str) or not expected_chain_name:
            raise ChainObservationError("an explicit chain name is required")
        if not isinstance(expected_spec_name, str) or not expected_spec_name:
            raise ChainObservationError("an explicit runtime spec name is required")
        if isinstance(expected_spec_version, bool) or not isinstance(expected_spec_version, int) or expected_spec_version < 0:
            raise ChainObservationError("an explicit runtime spec version is required")
        if not isinstance(expected_token_symbol, str) or not expected_token_symbol:
            raise ChainObservationError("an explicit display token symbol is required")
        if isinstance(expected_token_decimals, bool) or not isinstance(expected_token_decimals, int) or not 0 <= expected_token_decimals <= 38:
            raise ChainObservationError("an explicit token precision is required")
        if not isinstance(expected_asset_id, str) or not expected_asset_id or len(expected_asset_id) > 128:
            raise ChainObservationError("an explicit chain-scoped asset identifier is required")
        if not isinstance(watcher_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", watcher_id):
            raise ChainObservationError("watcher_id is invalid")
        self.expected_chain_name = expected_chain_name
        self.expected_spec_name = expected_spec_name
        self.expected_spec_version = expected_spec_version
        self.expected_token_symbol = expected_token_symbol
        self.expected_token_decimals = expected_token_decimals
        self.expected_asset_id = expected_asset_id
        self.watcher_id = watcher_id

    @staticmethod
    def _hash(value: Any, field: str) -> str:
        if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
            raise ChainObservationError(f"{field} must be a 32-byte 0x-prefixed hash")
        return value.lower()

    @staticmethod
    def _account_id(value: Any, field: str) -> str:
        if not isinstance(value, str) or not _ACCOUNT_ID_RE.fullmatch(value):
            raise ChainObservationError(f"{field} must be a raw 32-byte AccountId32")
        return value.lower()

    @staticmethod
    def _amount(value: Any) -> str:
        if isinstance(value, bool):
            raise ChainObservationError("transfer amount must be a positive base-unit integer")
        if isinstance(value, int):
            amount = value
        elif isinstance(value, str) and value.isascii() and value.isdigit():
            amount = int(value)
        else:
            raise ChainObservationError("transfer amount must be a positive base-unit integer")
        if amount <= 0 or amount > _U128_MAX:
            raise ChainObservationError("transfer amount is outside the Substrate u128 range")
        return str(amount)

    @staticmethod
    def _extrinsic_hash(extrinsic_hex: Any) -> str:
        if not isinstance(extrinsic_hex, str) or not extrinsic_hex.startswith("0x"):
            raise ChainObservationError("raw signed extrinsic bytes are required")
        encoded = extrinsic_hex[2:]
        if not encoded or len(encoded) % 2 or not re.fullmatch(r"[0-9a-fA-F]+", encoded):
            raise ChainObservationError("raw signed extrinsic bytes are malformed")
        return "0x" + hashlib.blake2b(bytes.fromhex(encoded), digest_size=32).hexdigest()

    def bind_beneficiary(self, user: str, address: str) -> None:
        """Install a local, operator-controlled address binding for observation only."""
        if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}", user):
            raise ChainObservationError("exchange account identifier is invalid")
        beneficiary = self._account_id(address, "beneficiary AccountId32")
        with self.ledger.transaction():
            account = self.conn.execute(
                "SELECT status, role FROM exchange_accounts WHERE user = ?", (user,)
            ).fetchone()
            if not account or account["status"] != "active" or account["role"] != "trader":
                raise ChainObservationError("beneficiary must belong to an active trader account")
            self.conn.execute(
                """INSERT INTO chain_deposit_bindings
                   (user, genesis_hash, asset_id, beneficiary_address, enabled, created_ns)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (user, self.expected_genesis_hash, self.expected_asset_id, beneficiary, time.time_ns()),
            )

    def _verify_block_identity(self, block: FinalizedBlock, genesis: str) -> None:
        if genesis != self.expected_genesis_hash:
            raise ChainObservationError("network genesis hash does not match the configured network")
        if not isinstance(block.chain_name, str) or not isinstance(block.spec_name, str):
            raise ChainObservationError("chain and runtime identity metadata must be text")
        if isinstance(block.spec_version, bool) or not isinstance(block.spec_version, int):
            raise ChainObservationError("runtime spec version must be an integer")
        if not isinstance(block.token_symbol, str):
            raise ChainObservationError("token symbol metadata must be text")
        if isinstance(block.token_decimals, bool) or not isinstance(block.token_decimals, int):
            raise ChainObservationError("token precision metadata must be an integer")
        if block.chain_name != self.expected_chain_name:
            raise ChainObservationError("chain name does not match the configured network")
        if block.spec_name != self.expected_spec_name or block.spec_version != self.expected_spec_version:
            raise ChainObservationError("runtime identity/version changed; stop and review before observing transfers")
        if block.token_symbol != self.expected_token_symbol or block.token_decimals != self.expected_token_decimals:
            raise ChainObservationError("runtime display asset properties do not match the configured asset")

    def _verify_event(self, event: FinalizedTransferEvent) -> tuple[str, str, str, str] | None:
        if event.pallet != "Balances" or event.event_name != "Transfer" or event.asset_id != self.expected_asset_id:
            return None
        if event.extrinsic_success is not True:
            return None
        if event.system_success_phase_extrinsic_index != event.extrinsic_index:
            raise ChainObservationError("System.ExtrinsicSuccess does not match the transfer extrinsic")
        if event.call_pallet != "Balances" or event.call_name not in _ALLOWED_TRANSFER_CALLS:
            raise ChainObservationError("transfer event is not backed by an allowlisted Balances transfer call")
        if event.phase_extrinsic_index != event.extrinsic_index:
            raise ChainObservationError("transfer event phase does not match its extrinsic")
        if self._extrinsic_hash(event.extrinsic_hex) != self._hash(event.tx_hash, "transaction hash"):
            raise ChainObservationError("transaction hash does not match the raw finalized extrinsic")
        event_amount = self._amount(event.amount_base_units)
        call_amount = self._amount(event.call_amount_base_units)
        if event_amount != call_amount:
            raise ChainObservationError("Balances.Transfer amount does not match the signed call")
        sender = self._account_id(event.sender, "transfer sender")
        beneficiary = self._account_id(event.beneficiary, "transfer beneficiary")
        call_sender = self._account_id(event.call_sender, "signed call origin")
        call_beneficiary = self._account_id(event.call_beneficiary, "signed call destination")
        if sender != call_sender:
            raise ChainObservationError("Balances.Transfer sender does not match the signed call origin")
        if beneficiary != call_beneficiary:
            raise ChainObservationError("Balances.Transfer recipient does not match the signed call destination")
        return event_amount, self._hash(event.tx_hash, "transaction hash"), sender, beneficiary

    def ingest_block(self, block: FinalizedBlock) -> dict:
        """Atomically record eligible events and advance the finalized block cursor."""
        if not isinstance(block, FinalizedBlock):
            raise TypeError("block must be a FinalizedBlock")
        genesis = self._hash(block.genesis_hash, "block genesis hash")
        block_hash = self._hash(block.block_hash, "block hash")
        parent_hash = self._hash(block.parent_hash, "parent hash")
        self._verify_block_identity(block, genesis)
        if isinstance(block.height, bool) or not isinstance(block.height, int) or block.height < 0:
            raise ChainObservationError("block height must be a non-negative integer")
        if block.finalized is not True:
            raise ChainObservationError("unfinalized blocks cannot create deposit observations")

        created: list[dict] = []
        replayed = 0
        ignored = 0
        with self.ledger.transaction():
            cursor = self.conn.execute(
                "SELECT genesis_hash, last_height, last_block_hash FROM chain_observer_cursors WHERE watcher_id = ?",
                (self.watcher_id,),
            ).fetchone()
            if cursor:
                if str(cursor["genesis_hash"]).lower() != genesis:
                    raise ChainObservationError("saved observer cursor belongs to a different network")
                last_height = int(cursor["last_height"])
                last_hash = str(cursor["last_block_hash"]).lower()
                if block.height == last_height and block_hash == last_hash:
                    replaying_current = True
                else:
                    replaying_current = False
                    if block.height != last_height + 1:
                        raise ChainObservationError("finalized block cursor must advance one height at a time")
                    if parent_hash != last_hash:
                        raise ChainObservationError("finalized block parent does not match the saved cursor")
            else:
                replaying_current = False
                if block.height != 0 or block_hash != genesis or parent_hash != "0x" + "0" * 64:
                    raise ChainObservationError("observer must begin at the configured genesis block")

            for event in block.transfers:
                if not isinstance(event, FinalizedTransferEvent):
                    raise ChainObservationError("block events must use the FinalizedTransferEvent type")
                verified = self._verify_event(event)
                if verified is None:
                    ignored += 1
                    continue
                amount, tx_hash, sender, beneficiary_address = verified
                if isinstance(event.extrinsic_index, bool) or not isinstance(event.extrinsic_index, int) or event.extrinsic_index < 0:
                    raise ChainObservationError("extrinsic index is invalid")
                if isinstance(event.event_index, bool) or not isinstance(event.event_index, int) or event.event_index < 0:
                    raise ChainObservationError("event index is invalid")
                if sender == beneficiary_address:
                    ignored += 1
                    continue
                binding = self.conn.execute(
                    """SELECT b.user, a.status FROM chain_deposit_bindings b
                       JOIN exchange_accounts a ON a.user = b.user
                       WHERE b.genesis_hash = ? AND b.asset_id = ?
                         AND b.beneficiary_address = ? AND b.enabled = 1""",
                    (genesis, self.expected_asset_id, beneficiary_address),
                ).fetchone()
                if not binding or binding["status"] != "active":
                    ignored += 1
                    continue
                identity = (genesis, tx_hash, event.event_index)
                payload = (
                    self.expected_asset_id, block.height, block_hash, event.extrinsic_index,
                    sender, beneficiary_address, str(binding["user"]), amount,
                )
                existing = self.conn.execute(
                    """SELECT asset_id, block_height, block_hash, extrinsic_index,
                              sender_address, beneficiary_address, account_id, amount_base_units
                       FROM chain_deposit_observations
                       WHERE genesis_hash = ? AND tx_hash = ? AND event_index = ?""",
                    identity,
                ).fetchone()
                if existing:
                    prior = (
                        str(existing["asset_id"]), int(existing["block_height"]),
                        str(existing["block_hash"]).lower(), int(existing["extrinsic_index"]),
                        str(existing["sender_address"]), str(existing["beneficiary_address"]),
                        str(existing["account_id"]), str(existing["amount_base_units"]),
                    )
                    if prior != payload:
                        raise ChainObservationError("duplicate chain event conflicts with the saved observation")
                    replayed += 1
                    continue
                self.conn.execute(
                    """INSERT INTO chain_deposit_observations
                       (purpose, genesis_hash, asset_id, block_height, block_hash,
                        tx_hash, extrinsic_index, event_index, sender_address,
                        beneficiary_address, account_id, amount_base_units, status,
                        ledger_credit_event_id, created_ns)
                       VALUES ('observation_only', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'pending_settlement_review', NULL, ?)""",
                    (genesis, self.expected_asset_id, block.height, block_hash, tx_hash,
                     event.extrinsic_index, event.event_index, sender,
                     beneficiary_address, str(binding["user"]), amount, time.time_ns()),
                )
                created.append({
                    "genesis_hash": genesis,
                    "asset_id": self.expected_asset_id,
                    "block_height": block.height,
                    "block_hash": block_hash,
                    "tx_hash": tx_hash,
                    "event_index": event.event_index,
                    "account_id": str(binding["user"]),
                    "amount_base_units": amount,
                    "status": "pending_settlement_review",
                    "ledger_credit_event_id": None,
                })

            if not replaying_current:
                self.conn.execute(
                    """INSERT INTO chain_observer_cursors(watcher_id, genesis_hash, last_height, last_block_hash, updated_ns)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(watcher_id) DO UPDATE SET
                         genesis_hash=excluded.genesis_hash,
                         last_height=excluded.last_height,
                         last_block_hash=excluded.last_block_hash,
                         updated_ns=excluded.updated_ns""",
                    (self.watcher_id, genesis, block.height, block_hash, time.time_ns()),
                )

        return {
            "created": created,
            "created_count": len(created),
            "replayed_count": replayed,
            "ignored_count": ignored,
            "cursor_height": block.height,
            "ledger_credits": 0,
            "observation_only": True,
        }

    def observations_for(self, user: str) -> list[dict]:
        with self.ledger._lock:
            rows = self.conn.execute(
                """SELECT genesis_hash, asset_id, block_height, block_hash, tx_hash,
                          event_index, sender_address, beneficiary_address, account_id,
                          amount_base_units, status, ledger_credit_event_id
                   FROM chain_deposit_observations WHERE account_id = ? ORDER BY observation_id""",
                (user,),
            ).fetchall()
        return [dict(row) for row in rows]
