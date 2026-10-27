# Kenan Ecosystem — verified status (2026-10-27)

This status records local verification for the wallet/RPC and DEMO-exchange change. It is not a production-readiness claim.

## Verified checks and artifact

- **Python:** `python3 -m unittest discover -s tests -v` — **32/32 passed**. Includes bind-address safety, signed devnet transfers, persistence/replay checks, HTTP RPC integration and DEMO order matching.
- **Rust:** `cargo test --locked --manifest-path rust-core/Cargo.toml` — **2/2 passed**. `rust-core/` remains a small signing/address library, not a blockchain node.
- **Android:** `:app:testDebugUnitTest` — **15/15 passed**; `:app:lintDebug` — **0 errors, 8 advisory warnings**; `:app:assembleDebug` — passed. The lint warnings are target-SDK/dependency-update suggestions; dependencies were not upgraded blindly across the existing Compose/Kotlin toolchain.
- **Debug APK:** `android-app/app/build/outputs/apk/debug/app-debug.apk`; package `com.kenan.wallet`, version `0.2.0-devnet` (code 2), **12,639,007 bytes**. APK Signature Scheme v2 verified. SHA-256: `dfc4eef8f99e3bae5578c7eefcf616f0c2ecc3e23dd33fbeec7f2dfc0eb93d97`. It is signed with the standard Android Debug certificate, not a project release key.
- **CI:** `.github/workflows/ci.yml` runs Python, Rust and Android tests/lint/Debug build and uploads the APK as a 7-day artifact. GitHub Actions run [40427266724](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40427266724) completed successfully for commit `521b63d` on `fix/kenan-wallet-rpc-ui-2026-10-27`; Android, Python and Rust jobs all passed.

## Implemented in this change — development only

- `android-app/`: explicit RPC endpoint resolution. The emulator may use `10.0.2.2`; a physical phone gets no guessed LAN IP and must be configured with the computer's private Wi-Fi IP and port `8545`. UI distinguishes unconfigured, unreachable and empty-history states. No API is exposed to a public address by default.
- `blockchain/node_server.py` and `network_utils.py`: node binding is limited to explicit local/private addresses; wildcard/public binds are rejected. `docs/ANDROID.md` documents the local firewall and phone setup.
- Wallet storage now passes the stored AES-GCM IV into decryption and has tamper/round-trip JVM tests. The manifest disables backup and supplies explicit backup/data-extraction exclusions.
- Android Compose wallet screens were redesigned and localized in Arabic/English. Chain balances and transfer history use RPC; the price chart does not fabricate prices. Transfer review rechecks nonce/balance/fee, signs locally, submits once, and verifies the returned transaction.
- `backend/`: a separate, explicitly labelled in-memory DEMO market/order service is connected to the app. It is not settled on-chain and resets when the backend restarts; it is not a real exchange or durable ledger.

## Polkadot SDK baseline — verified locally; Phase 2 remains in progress

- `polkadot/sdk/` is a Git submodule pinned to official release commit `cd87d0b95e54c8ffc374334386f3f2b2c7a61f54` (`polkadot-stable2603-7` / `polkadot-v1.22.7`). The unmodified official Solochain node built successfully with Rust `1.93.0`, locked dependencies and the `wasm32v1-none` target. The temporary 5.7 GB build tree was removed after testing; the pinned source remains in the repository.
- A real local development node served loopback RPC, produced/finalized blocks and accepted a signed Alice-to-Bob transfer of `1,000,000,000` base units. The recipient increased by the exact amount; the sender paid a calculated `759,936,145` base-unit fee; nonce changed `0 → 1`. Transaction hash: `0xd586064626c00169fdae429ed50e797ee899eb87a3aa14c3f91cbd707b61bc68`.
- Regression checks after this change passed locally: Python **32/32**, Rust core **2/2**, and Gradle `:app:testDebugUnitTest`, `:app:lintDebug`, `:app:assembleDebug` (**BUILD SUCCESSFUL**; 51 of 53 tasks were up-to-date).
- This is an upstream template baseline, not yet a Kenan-specific runtime, approved currency/genesis, or wallet integration. A separate SDK build-and-smoke CI job has been added; hosted GitHub CI has not been triggered from this local change.

## Not verified or not implemented yet

- **No Android emulator or physical phone was connected.** Installation, Android Keystore behavior on-device, LAN reachability from a phone, QR scanning, accessibility/small-screen behavior and a UI-to-live-node round trip remain to be tested on a real device.
- **Polkadot SDK follow-up gates:** wrong-signature/replay rejection, restart/persistence recovery, complete RPC submission/status coverage, Android wallet integration and outage/delayed-finality handling remain unverified. No Kenan-specific runtime/genesis or tokenomics decision has been implemented or approved. The Python devnet remains the current wallet network until the compatibility gates pass.
- **Exchange:** no production custody, durable double-entry ledger, external deposit/withdrawal rails, settlement, payment verification or real-money trading. DEMO balances have no monetary value.
- No release signing key, independent security audit or production operations review has been configured.

All balances and transactions are test data. Do not expose the node or faucet to untrusted networks or present this project as production financial infrastructure.
