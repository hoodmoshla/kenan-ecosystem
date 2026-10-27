# Kenan Ecosystem — verified status (2026-10-27)

This status records local verification for the wallet/RPC and DEMO-exchange change. It is not a production-readiness claim.

## Verified checks and artifact

- **Python:** `python3 -m unittest discover -s tests -v` — **32/32 passed**. Includes bind-address safety, signed devnet transfers, persistence/replay checks, HTTP RPC integration and DEMO order matching.
- **Rust:** `cargo test --locked --manifest-path rust-core/Cargo.toml` — **2/2 passed**. `rust-core/` remains a small signing/address library, not a blockchain node.
- **Android:** `:app:testDebugUnitTest` — **15/15 passed**; `:app:lintDebug` — **0 errors, 8 advisory warnings**; `:app:assembleDebug` — passed. The lint warnings are target-SDK/dependency-update suggestions; dependencies were not upgraded blindly across the existing Compose/Kotlin toolchain.
- **Debug APK:** `android-app/app/build/outputs/apk/debug/app-debug.apk`; package `com.kenan.wallet`, version `0.2.0-devnet` (code 2), **12,639,007 bytes**. APK Signature Scheme v2 verified. SHA-256: `dfc4eef8f99e3bae5578c7eefcf616f0c2ecc3e23dd33fbeec7f2dfc0eb93d97`. It is signed with the standard Android Debug certificate, not a project release key.
- **CI:** `.github/workflows/ci.yml` runs Python, Rust and Android tests/lint/Debug build and uploads the APK as a 7-day artifact. The branch-specific Actions result must be checked after the Git push.

## Implemented in this change — development only

- `android-app/`: explicit RPC endpoint resolution. The emulator may use `10.0.2.2`; a physical phone gets no guessed LAN IP and must be configured with the computer's private Wi-Fi IP and port `8545`. UI distinguishes unconfigured, unreachable and empty-history states. No API is exposed to a public address by default.
- `blockchain/node_server.py` and `network_utils.py`: node binding is limited to explicit local/private addresses; wildcard/public binds are rejected. `docs/ANDROID.md` documents the local firewall and phone setup.
- Wallet storage now passes the stored AES-GCM IV into decryption and has tamper/round-trip JVM tests. The manifest disables backup and supplies explicit backup/data-extraction exclusions.
- Android Compose wallet screens were redesigned and localized in Arabic/English. Chain balances and transfer history use RPC; the price chart does not fabricate prices. Transfer review rechecks nonce/balance/fee, signs locally, submits once, and verifies the returned transaction.
- `backend/`: a separate, explicitly labelled in-memory DEMO market/order service is connected to the app. It is not settled on-chain and resets when the backend restarts; it is not a real exchange or durable ledger.

## Not verified or not implemented yet

- **No Android emulator or physical phone was connected.** Installation, Android Keystore behavior on-device, LAN reachability from a phone, QR scanning, accessibility/small-screen behavior and a UI-to-live-node round trip remain to be tested on a real device.
- **Polkadot SDK:** official source review is recorded in `docs/POLKADOT_SDK_RESEARCH.md`; `polkadot-stable2603-7` is an evaluation candidate only. No SDK workspace, runtime, node, validator, CI lane or wallet adapter has been built. Python devnet remains the sole working chain.
- **Exchange:** no production custody, durable double-entry ledger, external deposit/withdrawal rails, settlement, payment verification or real-money trading. DEMO balances have no monetary value.
- No release signing key, independent security audit or production operations review has been configured.

All balances and transactions are test data. Do not expose the node or faucet to untrusted networks or present this project as production financial infrastructure.
