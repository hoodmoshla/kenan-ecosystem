# Operations — local devnet and remaining launch gates

## Run locally (test tokens only)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 -m blockchain.node_server --host 127.0.0.1 --port 8545 --data data/chain.json
KENAN_NODE_URL=http://127.0.0.1:8545 python3 -m backend.app --port 8000
python3 -m unittest discover -s tests -v
```

To run the local Kenan Polkadot Runtime for Android/observer tests, use `./polkadot/kenan-runtime/run-local.sh`; it keeps RPC/P2P on loopback and reverses its temporary SDK overlay on shutdown. It uses a separate test-only chain data path. Follow the phone/Wi-Fi instructions in [ANDROID.md](ANDROID.md); do not expose RPC directly to LAN.

The node binds to loopback by default. Faucet and empty-block mining are also loopback-only; a non-loopback bind requires the operator to configure `KENAN_FAUCET_TOKEN` for those two endpoints and pass `X-Faucet-Token`. Keep any such endpoint on a private test network; the token is not a replacement for transport security or an access-control layer for production. Faucet issuance is recorded and capped at 1000 KEN per address.

## State file and restart

The node stores schema-v2 blocks and cached account snapshots. On each restart it rebuilds balances, nonces, issued test tokens, replay IDs, fees and transaction lookup from the ledger, checks signatures/hashes/supply and compares the result with the snapshots. It refuses to start on inconsistent state; it does not reset or overwrite a corrupt file.

Earlier chain files did not record faucet issuance in blocks. If an older file fails replay/snapshot checks, preserve a copy and stop for a deliberate migration/reconciliation decision. Do not delete it and silently start a new genesis. The state file is still a single-node devnet file, not a consensus-backed or adversarially tamper-proof ledger; use one node process per file. No `data/chain.json` existed in the checked workspace at the time of this change.

## Before any public launch

- Build a separate Polkadot SDK node/runtime and testnet, with an explicit migration gate; keep Python devnet available until all gates pass.
- Harden the persistent local DEMO prototype's authentication/authorization and complete tested recovery/backup/restore operations; current accounts/sessions are loopback-only test safeguards, not production identity.
- Define token issuance, fee model, governance, validator key custody and upgrade procedures; tokenomics remains a draft.
- Complete independent security review, penetration testing, legal/licensing review, TLS/production endpoint design, incident response and release-key verification.
- Keep real funds, listings and bridge disabled until their separate security and legal requirements are met.
