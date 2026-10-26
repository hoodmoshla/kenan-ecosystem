# Kenan Ecosystem — verified status (2026-10-26)

This status covers the checked repository snapshot and the work verified in this change. It does not imply production readiness.

## Verified

- **Python:** `python3 -m unittest discover -s tests -v` — **23/23 passed**. Tests cover BIP39 reference wallet, signed transfers/nonces/fees, HTTP integration, replay rejection after reload, faucet ledger/cap, snapshot and transaction tampering rejection, demo exchange behavior, and prototype modules.
- **Rust:** `cargo test --locked --manifest-path rust-core/Cargo.toml` — **2/2 unit tests passed**, 0 doc-tests. This remains a small signing/address library, not a chain node.
- **Android:** `:app:testDebugUnitTest` — **4/4 passed**. The known BIP39 address, public key, Ed25519 signature, and transaction hash match the Python reference. `:app:lintDebug` and `:app:assembleDebug` are the CI build gates. The Debug APK was verified with `apksigner` and `aapt`; it is signed with the standard Debug key, not a release key. **No emulator or physical device was connected**, so installation, Android Keystore runtime behavior, QR scanning by another device, and UI-to-node interaction were not tested on-device.
- **Current local Debug APK:** `android-app/app/build/outputs/apk/debug/app-debug.apk`; package `com.kenan.wallet`, version `0.1.0-devnet`, **12,845,906 bytes**. `apksigner` verified APK Signature Scheme v2; SHA-256: `d21715d16c2c5caea2d919b49cf6b4a5a6513a3e0738f68bf460f338d10af12f`.
- **CI:** `.github/workflows/ci.yml` runs Python, Rust, and Android tests/lint/Debug APK build; the Android APK is uploaded as a short-lived workflow artifact. Check the linked Actions run for the commit-specific result.

## What the repository provides

- `blockchain/`: a single-process Python devnet with Ed25519 signed transfers, sequential nonces, fixed fee of 0.001 KEN, half-fee burn, hash-linked blocks, and JSON persistence. Schema v2 rebuilds balances, nonces, issued faucet amounts, supply, burned amount, replay set, and transaction lookup from block events on restart; it rejects snapshot/ledger mismatches and invalid signatures. Faucet events are recorded and capped at 1000 KEN per address. Faucet and empty-block mining are loopback-only by default; a non-loopback operator must configure `KENAN_FAUCET_TOKEN` and send `X-Faucet-Token`. This is still a local devnet, not consensus or a cryptographic trust anchor; a party able to rewrite the whole local ledger can recompute its hashes.
- **Older chain state:** previous snapshots that do not record faucet issuance cannot be safely reconstructed. On mismatch the new loader refuses to trust or overwrite the file. No `data/chain.json` was present in the checked workspace; only an audit log was present. Preserve and back up any older state before starting the new node; do not delete it to silence a startup error.
- `wallet-core/`: Python BIP39 reference with 24-word creation/recovery and Ed25519 local signing.
- `android-app/`: Android wallet interoperable with the Python reference. Mnemonic is encrypted at rest with AES-GCM and a non-exportable Android Keystore AES key; transaction signing uses the matching locally-derived Ed25519 key. The UI shows live RPC balance/address, a QR receive address, and recipient/amount/fee/total before explicit local signing. On confirmation it re-reads nonce/balance, submits once, and verifies the transaction by hash and fields via RPC. Network errors are not auto-resubmitted.
- `backend/`: HTTP API and in-memory DEMO matching engine; demo balances and orders disappear on restart and are not settled to chain.
- `explorer/`: read-only views for the Python devnet.
- `docs/POLKADOT.md`: staged plan only; no Polkadot SDK runtime or node has been built.

## Still incomplete / blockers

- Polkadot SDK/Substrate runtime, node, validators, consensus and the network adapter/migration. Python devnet remains the current test model until the documented gates pass.
- Android device/emulator installation, Android Keystore instrumentation tests, QR verification on a second device, accessibility/small-screen checks, and a full UI-to-live-node flow test.
- Release signing with a project-owned key whose fingerprint is verified; HTTPS endpoint configuration for a release; independent security review and penetration testing.
- Production custody, mainnet, real KEN issuance/listing, real-money trading, audited bridge, durable exchange, verified payment receipts, external price feed, and production operations/backups.

All balances and transactions are devnet test data. Do not expose the node or faucet to an untrusted network or present the project as production financial infrastructure.
