# Kenan Ecosystem — verified status (2026-10-26)

This status describes only the GitHub repository snapshot and work verified in this session. No separate pre-existing local project copy was available to inspect.

## Verified in this session

- **Python:** `python3 -m unittest discover -s tests -v` — 17 tests passed. Coverage includes BIP39 wallet creation/restore/signing, devnet transfers/nonces/fees, HTTP integration, demo exchange behavior, and prototype modules. The tests use generated test keys and temporary chain state.
- **Rust:** `cargo test --locked` in `rust-core/` — 2 unit tests passed; 0 doc-tests. This is a small signing/address library, not a blockchain node.
- **Android:** `./gradlew --no-daemon :app:assembleDebug` — BUILD SUCCESSFUL with Gradle 8.9, JDK 21, and the installed Android SDK. The debug APK was verified with `apksigner` and `aapt`; it was not installed on a device because no emulator or device was connected. This is a debug build signed with the standard debug certificate, not a release build.
- **CI:** GitHub Actions run [40388581691](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40388581691) completed successfully on the repair branch: Python tests, Rust tests, and Android debug APK build all passed. The initial run failed before Gradle because the old Android setup action requested a removed SDK package; setup was updated and the follow-up run passed.

## What the code currently provides

- `blockchain/`: a local Python devnet with Ed25519-signed transfers, sequential nonces, a fixed 0.001 KEN fee, half-fee burn, hash-linked block headers, JSON persistence, and HTTP RPC. This is a test implementation, not consensus-secure infrastructure. The balance/nonce snapshot in the JSON file is not independently rebuilt from a complete ledger; do not trust edited or externally supplied state files.
- `wallet-core/`: a Python BIP39 reference wallet supporting 24-word phrase creation, restore, backup proof, and local Ed25519 signing. It does not implement the Android user's wallet storage.
- `rust-core/`: signing/address-derivation primitives with two unit tests. There is no Polkadot SDK/Substrate node here.
- `backend/`: HTTP APIs and a demo matching engine. Exchange balances and orders are in memory and disappear when the process restarts; trades are not settled to the chain. The engine remains DEMO-only.
- `android-app/`: a Kotlin/Compose RTL shell. It can read account/chain information through the Python node client when run in a suitable development environment. Wallet creation/recovery, Android Keystore storage, transaction signing/submission from the UI, QR receive, real exchange flows, and device-level testing remain incomplete.
- `explorer/` and the node `/explorer` page: read-only views backed by the Python devnet RPC. They do not support a Polkadot chain.
- `admin/`: a monitoring page for a single token-gated backend status endpoint, not a complete administration console.
- `backend/modules/features.py`: early/demo modules. Intelligence is rule-based; Governance is off-chain signalling; Recovery is a proposal state machine; Pay does not yet verify the transaction against the node; Insights accepts caller-supplied holdings; Bridge is deliberately disabled.

## Not implemented / release blockers

- Polkadot SDK/Substrate runtime, node, validators, consensus, and network migration.
- Mainnet, public KEN issuance/listing, real-money trading, custody, and audited bridge.
- Production wallet creation/recovery and Android Keystore integration.
- Persistent and auditable exchange backend, verified on-chain payment receipts, real price feed, and external LLM provider.
- Independent security review, penetration testing, legal/licensing review, production operations, backups/restore drills, and release signing key verified against a known project certificate.

All balances and transactions in this Python chain are devnet test data. Do not expose the faucet or node to an untrusted/public network and do not present this project as production financial infrastructure.
