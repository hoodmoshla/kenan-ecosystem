# Kenan Ecosystem — KenanWallet / KEN (devnet)

Arabic README: [README.ar.md](README.ar.md)

**Status: devnet / demo only. No real money, no mainnet, no public listing.**
See [docs/STATUS.md](docs/STATUS.md) for exactly what was verified by tests
and what is still pending — do not present demo balances as real.

## What exists here
- `blockchain/` — devnet chain core + HTTP node: Ed25519-signed KEN
  transfers, fees (50% burn), hash-chained blocks, persistent state.
- `wallet-core/` — BIP39 self-custody wallet: create/restore/sign locally.
- `rust-core/` — Rust Ed25519 signing + address derivation (same format).
- `backend/` — API + DEMO exchange matching engine + Circles, Pay,
  Recovery (proposal machine, no key custody), Academy, Insights, Shield,
  Governance (signalling), Intelligence (rule-based, no keys), Bridge (disabled).
- `explorer/` + node `/explorer` — block/tx/account/supply viewer from live RPC.
- `android-app/` — Kotlin/Compose RTL wallet source; debug APK builds, but
  wallet creation/signing and device testing are not complete.
- `tests/` — 17 automated tests covering chain, wallet, exchange, modules, HTTP.

## Quick start
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 -m blockchain.node_server --port 8545 --data data/chain.json
KENAN_NODE_URL=http://127.0.0.1:8545 python3 -m backend.app --port 8000
python3 scripts/demo.py   # end-to-end devnet demo with test wallets
python3 -m unittest discover -s tests -v
```
Explorer: http://127.0.0.1:8545/explorer · API: [docs/API.md](docs/API.md)

## Rules that are enforced, not just documented
- Balances come from chain state, never from an app database.
- Demo exchange balances are labelled DEMO everywhere and isolated.
- No private key/seed ever leaves the signing process or reaches the backend/AI.
- No button reports success for an operation that was not executed and verified.
- Bridge transfers are refused until an independent audit exists.
