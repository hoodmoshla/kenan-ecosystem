#!/usr/bin/env python3
"""Exercise the Exchange observer with a finalized Kenan Runtime RPC bundle."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.exchange.auth import ExchangeAuth  # noqa: E402
from backend.exchange.chain_deposits import (  # noqa: E402
    ChainDepositObserver,
    FinalizedBlock,
    FinalizedTransferEvent,
)
from backend.exchange.engine import Engine  # noqa: E402


def observer_for(engine: Engine, bundle: dict) -> ChainDepositObserver:
    return ChainDepositObserver(
        engine.ledger,
        expected_genesis_hash=bundle["genesis_hash"],
        expected_chain_name=bundle["chain_name"],
        expected_spec_name=bundle["spec_name"],
        expected_spec_version=bundle["spec_version"],
        expected_token_symbol=bundle["token_symbol"],
        expected_token_decimals=bundle["token_decimals"],
        expected_asset_id=bundle["asset_id"],
        watcher_id="kenan-runtime-ci-observer-01",
    )


def block_from(value: dict) -> FinalizedBlock:
    events = tuple(FinalizedTransferEvent(**event) for event in value["transfers"])
    return FinalizedBlock(
        genesis_hash=value["genesis_hash"],
        height=value["height"],
        block_hash=value["block_hash"],
        parent_hash=value["parent_hash"],
        finalized=value["finalized"],
        chain_name=value["chain_name"],
        spec_name=value["spec_name"],
        spec_version=value["spec_version"],
        token_symbol=value["token_symbol"],
        token_decimals=value["token_decimals"],
        transfers=events,
    )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: test-exchange-observer.py <finalized-rpc-bundle.json>")
    bundle_path = Path(sys.argv[1])
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    blocks = bundle["blocks"]
    if not blocks:
        raise AssertionError("RPC adapter returned no finalized block history")
    if bundle["token_symbol"] != "UNIT" or bundle["token_decimals"] != 12:
        raise AssertionError("test runtime asset metadata changed; do not infer KEN mapping")
    target_tx_hash = bundle["target_tx_hash"].lower()

    with tempfile.TemporaryDirectory(prefix="kenan-exchange-observer-") as directory:
        path = str(Path(directory) / "demo-exchange.sqlite3")
        engine = Engine(path)
        auth = ExchangeAuth(engine.ledger)
        auth.register("observer-test-user", "ephemeral ci-only observer password")
        observer = observer_for(engine, bundle)
        observer.bind_beneficiary("observer-test-user", bundle["target_beneficiary"])

        last_result = None
        for raw_block in blocks:
            last_result = observer.ingest_block(block_from(raw_block))
        rows = observer.observations_for("observer-test-user")
        assert len(rows) == 1, f"Expected one pending observation, got {len(rows)}"
        row = rows[0]
        assert str(row["tx_hash"]).lower() == target_tx_hash
        assert row["beneficiary_address"] == bundle["target_beneficiary"]
        assert row["amount_base_units"] == str(bundle["target_amount_base_units"])
        assert row["status"] == "pending_settlement_review"
        assert row["ledger_credit_event_id"] is None
        assert engine.balances_snapshot("observer-test-user") == {}, "chain observation must not credit the trading ledger"
        assert engine.reconcile()["ok"]
        assert last_result is not None
        engine.close()

        recovered = Engine(path)
        try:
            recovered_observer = observer_for(recovered, bundle)
            replay = recovered_observer.ingest_block(block_from(blocks[-1]))
            assert replay["created_count"] == 0
            assert replay["replayed_count"] >= 1
            recovered_rows = recovered_observer.observations_for("observer-test-user")
            assert len(recovered_rows) == 1
            assert recovered.balances_snapshot("observer-test-user") == {}
            assert recovered.reconcile()["ok"]
        finally:
            recovered.close()

    print(
        "PASS: finalized Kenan Runtime transfer observed once as pending; "
        "DEMO ledger credit=0; duplicate replay and restart recovery verified."
    )
    print("NOTE: the test runtime exposes UNIT/12. This is not an approved KEN asset mapping.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
