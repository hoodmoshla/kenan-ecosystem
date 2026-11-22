# Kenan Ecosystem — KenanWallet / KEN (devnet)

Arabic README: [README.ar.md](README.ar.md)

**Status: development devnet and demos only. No real money, mainnet, public listing, or production security review.** See [docs/STATUS.md](docs/STATUS.md) for verified results and remaining blockers.

## What exists
- `blockchain/` — a single-node Python devnet with signed Ed25519 transfers, fixed fees, hash-linked blocks, persisted ledger events, and replay-based state reconstruction. Faucet issuance is explicitly recorded and capped per address.
- `wallet-core/` — the Python reference wallet; its BIP39/Ed25519 derivation is the compatibility target for Android.
- `android-app/` — Kotlin/Compose RTL wallet: create or restore a 24-word BIP39 wallet, encrypt the phrase with AES-GCM using an Android Keystore key, show live balance and a receive QR, preview recipient/amount/fee, sign locally, submit to the current Python devnet RPC, then verify the returned transaction. Debug build only; no device/emulator test has been performed.
- `rust-core/` — a small Rust signing/address library, not a chain node.
- `backend/` — API and DEMO exchange/matching modules; demo exchange balances are in-memory and not settled on-chain.
- `explorer/` and node `/explorer` — read-only views of the Python devnet.
- `tests/` — **23 Python tests**. Android has **4 JVM unit tests** for BIP39, signing, amount conversion, and Python compatibility. Rust has **2 tests**.

All KEN balances are devnet test data. The Python node is not distributed consensus infrastructure; do not expose it as a public financial service.

## Run the devnet

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 -m blockchain.node_server --port 8545 --data data/chain.json
KENAN_NODE_URL=http://127.0.0.1:8545 python3 -m backend.app --port 8000
python3 -m unittest discover -s tests -v
```

Explorer: <http://127.0.0.1:8545/explorer> · API details: [docs/API.md](docs/API.md)

## Build and test Android

With Android SDK 34 and JDK 17 or newer:

```bash
cd android-app
./gradlew --no-daemon :app:testDebugUnitTest :app:lintDebug :app:assembleDebug
```

The default Debug endpoint is `http://10.0.2.2:8545` for the Android emulator talking to a node on the host. Override at build time with `-PkenanNodeUrl=http://<reachable-devnet-host>:8545`. HTTP is allowed only in Debug; do not use a public or untrusted network. For setup and wallet-security notes see [docs/ANDROID.md](docs/ANDROID.md).

## Project rules

- Account balances are read from the node; the Android app keeps no local balance ledger.
- The Android signing key/phrase is not sent to the node or backend; the phrase is encrypted at rest, then used locally to sign.
- A transfer is not reported as successful until its hash and fields are read back and checked over RPC.
- DEMO exchange balances are separate from on-chain KEN; the bridge remains disabled.
