"""Security tests for local exchange identity and chain observation boundaries."""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest

from backend.exchange.auth import (
    AccountConflict,
    AccountPolicyError,
    AuthenticationError,
    ExchangeAuth,
)
from backend.exchange.chain_deposits import (
    ChainDepositObserver,
    ChainObservationError,
    FinalizedBlock,
    FinalizedTransferEvent,
)
from backend.exchange.demo_funding import LocalDemoFunder
from backend.exchange.engine import Engine

GENESIS = "0x" + "11" * 32
ZERO_HASH = "0x" + "00" * 32
BLOCK1 = "0x" + "22" * 32
BLOCK2 = "0x" + "33" * 32
ASSET_ID = "substrate:balances:native:UNIT:12"
CHAIN_NAME = "Kenan Development (TEST ONLY)"
SPEC_NAME = "kenan-test-runtime"
SPEC_VERSION = 1
TOKEN_SYMBOL = "UNIT"
TOKEN_DECIMALS = 12
ALICE_ACCOUNT = "0x" + "bb" * 32
SENDER_ACCOUNT = "0x" + "aa" * 32
OTHER_ACCOUNT = "0x" + "cc" * 32


def make_event(
    *, raw="0x0102030405", extrinsic_hex=None, sender=SENDER_ACCOUNT, beneficiary=ALICE_ACCOUNT,
    amount="12345678901234567890", asset_id=ASSET_ID, pallet="Balances",
    event_name="Transfer", call_pallet="Balances", call_name="transferKeepAlive",
    call_sender=None, call_beneficiary=None, call_amount=None, extrinsic_index=2,
    phase_extrinsic_index=None, system_success_phase_index=None, event_index=5, success=True,
):
    if call_sender is None:
        call_sender = sender
    if call_beneficiary is None:
        call_beneficiary = beneficiary
    if call_amount is None:
        call_amount = amount
    raw_bytes = bytes.fromhex(raw.removeprefix("0x"))
    tx_hash = "0x" + hashlib.blake2b(raw_bytes, digest_size=32).hexdigest()
    return FinalizedTransferEvent(
        tx_hash=tx_hash,
        extrinsic_hex=raw if extrinsic_hex is None else extrinsic_hex,
        extrinsic_index=extrinsic_index,
        phase_extrinsic_index=extrinsic_index if phase_extrinsic_index is None else phase_extrinsic_index,
        event_index=event_index,
        pallet=pallet,
        event_name=event_name,
        asset_id=asset_id,
        system_success_phase_extrinsic_index=(
            (extrinsic_index if system_success_phase_index is None else system_success_phase_index)
            if success else None
        ),
        sender=sender,
        beneficiary=beneficiary,
        amount_base_units=amount,
        call_pallet=call_pallet,
        call_name=call_name,
        call_sender=call_sender,
        call_beneficiary=call_beneficiary,
        call_amount_base_units=call_amount,
        extrinsic_success=success,
    )


def make_block(height, block_hash, parent_hash, transfers=(), *, finalized=True,
               genesis_hash=GENESIS, chain_name=CHAIN_NAME, spec_name=SPEC_NAME,
               spec_version=SPEC_VERSION, token_symbol=TOKEN_SYMBOL,
               token_decimals=TOKEN_DECIMALS):
    return FinalizedBlock(
        genesis_hash=genesis_hash,
        height=height,
        block_hash=block_hash,
        parent_hash=parent_hash,
        finalized=finalized,
        chain_name=chain_name,
        spec_name=spec_name,
        spec_version=spec_version,
        token_symbol=token_symbol,
        token_decimals=token_decimals,
        transfers=tuple(transfers),
    )


class TestExchangeAuth(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.auth = ExchangeAuth(self.engine.ledger)

    def tearDown(self):
        self.engine.close()

    def test_password_hash_is_salted_and_session_secret_is_not_stored(self):
        self.auth.register("alice", "correct horse battery staple")
        account = self.engine.conn.execute(
            "SELECT password_salt_hex, password_hash_hex, pbkdf2_iterations FROM exchange_accounts WHERE user='alice'"
        ).fetchone()
        self.assertEqual(int(account["pbkdf2_iterations"]), 600_000)
        self.assertEqual(len(account["password_salt_hex"]), 32)
        self.assertNotEqual(account["password_hash_hex"], "correct horse battery staple")
        session = self.auth.authenticate("alice", "correct horse battery staple")
        stored = self.engine.conn.execute("SELECT token_hash FROM exchange_sessions").fetchone()[0]
        self.assertNotEqual(stored, session["token"])
        self.assertEqual(stored, hashlib.sha256(session["token"].encode("ascii")).hexdigest())
        self.assertEqual(self.auth.principal(session["token"]), {"user": "alice", "role": "trader"})
        with self.assertRaises(AuthenticationError):
            self.auth.principal(session["token"] + "é")
        self.assertFalse(self.auth.revoke(session["token"] + "é"))
        self.assertTrue(self.auth.revoke(session["token"]))
        with self.assertRaises(AuthenticationError):
            self.auth.principal(session["token"])

    def test_bad_credentials_roles_and_password_policy_fail_closed(self):
        with self.assertRaises(AccountPolicyError):
            self.auth.register("bad user", "short")
        with self.assertRaises(AccountPolicyError):
            self.auth.create_account("alice", "correct horse battery staple", role="root")
        self.auth.register("alice", "correct horse battery staple")
        with self.assertRaises(AccountConflict):
            self.auth.register("alice", "another safe password")
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate("alice", "incorrect horse battery staple")
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate("missing", "incorrect horse battery staple")

    def test_legacy_username_is_reserved_not_claimable_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/exchange.sqlite3"
            first = Engine(path)
            LocalDemoFunder(first.ledger).grant("legacyuser", "DEMO_BASE", 5, "grant-legacy-user-01")
            first.close()

            # Model a preserved v1 database so the test exercises the real migration path.
            old_schema = sqlite3.connect(path)
            old_schema.execute("PRAGMA foreign_keys = OFF")
            for table in (
                "chain_deposit_observations", "chain_deposit_bindings",
                "exchange_sessions", "chain_observer_cursors", "exchange_accounts",
            ):
                old_schema.execute(f"DROP TABLE {table}")
            old_schema.execute("PRAGMA user_version = 1")
            old_schema.commit()
            old_schema.close()

            migrated = Engine(path)
            try:
                with self.assertRaises(AccountConflict):
                    ExchangeAuth(migrated.ledger).register("legacyuser", "correct horse battery staple")
                self.assertEqual(migrated.balances_snapshot("legacyuser")["DEMO_BASE"]["available_units"], 5)
                self.assertEqual(migrated.conn.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertTrue(migrated.reconcile()["ok"])
            finally:
                migrated.close()


class TestChainDepositObservation(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.auth = ExchangeAuth(self.engine.ledger)
        self.auth.register("alice", "correct horse battery staple")
        self.observer = ChainDepositObserver(
            self.engine.ledger,
            expected_genesis_hash=GENESIS,
            expected_chain_name=CHAIN_NAME,
            expected_spec_name=SPEC_NAME,
            expected_spec_version=SPEC_VERSION,
            expected_token_symbol=TOKEN_SYMBOL,
            expected_token_decimals=TOKEN_DECIMALS,
            expected_asset_id=ASSET_ID,
            watcher_id="test-observer-0001",
        )
        self.observer.bind_beneficiary("alice", ALICE_ACCOUNT)

    def tearDown(self):
        self.engine.close()

    def test_only_finalized_verified_bound_transfer_is_recorded_and_never_credited(self):
        genesis = make_block(0, GENESIS, ZERO_HASH)
        self.assertEqual(self.observer.ingest_block(genesis)["cursor_height"], 0)
        event = make_event()
        first = self.observer.ingest_block(make_block(1, BLOCK1, GENESIS, (event,)))
        self.assertEqual(first["created_count"], 1)
        self.assertEqual(first["ledger_credits"], 0)
        observation = self.observer.observations_for("alice")[0]
        self.assertEqual(observation["status"], "pending_settlement_review")
        self.assertIsNone(observation["ledger_credit_event_id"])
        self.assertEqual(observation["amount_base_units"], "12345678901234567890")
        self.assertEqual(self.engine.balances_snapshot("alice"), {})

        replay = self.observer.ingest_block(make_block(1, BLOCK1, GENESIS, (event,)))
        self.assertEqual(replay["created_count"], 0)
        self.assertEqual(replay["replayed_count"], 1)
        self.assertEqual(len(self.observer.observations_for("alice")), 1)

    def test_network_runtime_asset_and_finality_mismatches_are_rejected(self):
        with self.assertRaises(ChainObservationError):
            self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH, genesis_hash="0x" + "99" * 32))
        with self.assertRaises(ChainObservationError):
            self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH, finalized=False))
        with self.assertRaises(ChainObservationError):
            self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH, token_symbol="KEN"))
        with self.assertRaises(ChainObservationError):
            self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH, spec_version=2))
        self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH))
        with self.assertRaises(ChainObservationError):
            self.observer.ingest_block(make_block(2, BLOCK2, BLOCK1))
        self.assertEqual(self.observer.observations_for("alice"), [])

    def test_unbound_recipient_wrong_asset_failed_extrinsic_and_unsupported_event_are_ignored(self):
        self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH))
        events = (
            make_event(raw="0x111213", beneficiary=OTHER_ACCOUNT, amount=10),
            make_event(raw="0x212223", asset_id="native:KEN", amount=20),
            make_event(raw="0x313233", amount=30, success=False),
            make_event(raw="0x414243", pallet="Assets", event_name="Transferred", amount=40),
        )
        result = self.observer.ingest_block(make_block(1, BLOCK1, GENESIS, events))
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["ignored_count"], 4)
        self.assertEqual(self.engine.balances_snapshot("alice"), {})

    def test_signed_call_must_match_phase_event_sender_recipient_amount_and_hash(self):
        self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH))
        bad_events = (
            make_event(raw="0x515253", call_amount=999),
            make_event(raw="0x616263", phase_extrinsic_index=99),
            make_event(raw="0x626364", system_success_phase_index=99),
            make_event(raw="0x717273", call_sender=OTHER_ACCOUNT),
            make_event(raw="0x818283", call_beneficiary=OTHER_ACCOUNT),
            make_event(raw="0x919293", call_name="forceTransfer"),
            make_event(raw="0xa1a2a3", extrinsic_hex="0x010203"),
        )
        for index, event in enumerate(bad_events):
            with self.subTest(index=index):
                with self.assertRaises(ChainObservationError):
                    self.observer.ingest_block(make_block(1, BLOCK1, GENESIS, (event,)))
        self.assertEqual(self.observer.observations_for("alice"), [])
        self.assertEqual(self.engine.balances_snapshot("alice"), {})

    def test_duplicate_event_with_conflicting_payload_and_direct_record_mutation_fail(self):
        self.observer.ingest_block(make_block(0, GENESIS, ZERO_HASH))
        event = make_event(amount=10)
        self.observer.ingest_block(make_block(1, BLOCK1, GENESIS, (event,)))
        conflicting = make_event(amount=11)
        # Same raw extrinsic/hash/event identity, altered decoded payload must be rejected.
        next_block = make_block(2, BLOCK2, BLOCK1, (conflicting,))
        with self.assertRaises(ChainObservationError):
            self.observer.ingest_block(next_block)
        row = self.engine.conn.execute("SELECT observation_id FROM chain_deposit_observations").fetchone()
        with self.assertRaises(sqlite3.IntegrityError):
            self.engine.conn.execute("UPDATE chain_deposit_observations SET amount_base_units='999' WHERE observation_id=?", (row[0],))
        self.assertEqual(self.engine.balances_snapshot("alice"), {})

    def test_restart_recovers_cursor_and_observation_without_recredit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/exchange.sqlite3"
            first = Engine(path)
            auth = ExchangeAuth(first.ledger)
            auth.register("alice", "correct horse battery staple")
            observer = ChainDepositObserver(
                first.ledger, expected_genesis_hash=GENESIS, expected_chain_name=CHAIN_NAME,
                expected_spec_name=SPEC_NAME, expected_spec_version=SPEC_VERSION,
                expected_token_symbol=TOKEN_SYMBOL, expected_token_decimals=TOKEN_DECIMALS,
                expected_asset_id=ASSET_ID, watcher_id="restart-observer-001",
            )
            observer.bind_beneficiary("alice", ALICE_ACCOUNT)
            observer.ingest_block(make_block(0, GENESIS, ZERO_HASH))
            event = make_event(amount=50)
            observer.ingest_block(make_block(1, BLOCK1, GENESIS, (event,)))
            first.close()
            recovered = Engine(path)
            try:
                observer2 = ChainDepositObserver(
                    recovered.ledger, expected_genesis_hash=GENESIS, expected_chain_name=CHAIN_NAME,
                    expected_spec_name=SPEC_NAME, expected_spec_version=SPEC_VERSION,
                    expected_token_symbol=TOKEN_SYMBOL, expected_token_decimals=TOKEN_DECIMALS,
                    expected_asset_id=ASSET_ID, watcher_id="restart-observer-001",
                )
                replay = observer2.ingest_block(make_block(1, BLOCK1, GENESIS, (event,)))
                self.assertEqual(replay["replayed_count"], 1)
                self.assertEqual(len(observer2.observations_for("alice")), 1)
                self.assertEqual(recovered.balances_snapshot("alice"), {})
                self.assertTrue(recovered.reconcile()["ok"])
            finally:
                recovered.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
