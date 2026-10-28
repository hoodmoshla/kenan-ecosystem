# Kenan Ecosystem — verified status (2026-10-28)

This status records the current test-only project state. It is not a production-readiness or financial-safety claim.

## Verified checks and artifact

- **Python:** `python3 -m unittest discover -s tests -v` — **51/51 passed** on the Phase 4 branch. Includes Python Devnet regression checks plus exchange accounting, reservations, partial fills, idempotency, concurrent writers, rollback, restart recovery and HTTP restrictions.
- **Rust:** `cargo test --locked --manifest-path rust-core/Cargo.toml` — **2/2 passed**. `rust-core/` remains a small signing/address library, not a blockchain node.
- **Android:** `:app:testDebugUnitTest` — **15/15 passed**; `:app:lintDebug` — **0 errors, 8 advisory warnings**; `:app:assembleDebug` — passed. The lint warnings are target-SDK/dependency-update suggestions; dependencies were not upgraded blindly across the existing Compose/Kotlin toolchain.
- **Debug APK:** `android-app/app/build/outputs/apk/debug/app-debug.apk`; package `com.kenan.wallet`, version `0.2.0-devnet` (code 2), **12,639,007 bytes**. APK Signature Scheme v2 verified. SHA-256: `dfc4eef8f99e3bae5578c7eefcf616f0c2ecc3e23dd33fbeec7f2dfc0eb93d97`. It is signed with the standard Android Debug certificate, not a project release key.
- **CI:** `.github/workflows/ci.yml` runs Python, Rust and Android tests/lint/Debug build and uploads the APK as a 7-day artifact. GitHub Actions run [40427266724](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40427266724) completed successfully for commit `521b63d` on `fix/kenan-wallet-rpc-ui-2026-10-27`; Android, Python and Rust jobs all passed.

## Implemented in this change — development only

- `android-app/`: explicit RPC endpoint resolution. The emulator may use `10.0.2.2`; a physical phone gets no guessed LAN IP and must be configured with the computer's private Wi-Fi IP and port `8545`. UI distinguishes unconfigured, unreachable and empty-history states. No API is exposed to a public address by default.
- `blockchain/node_server.py` and `network_utils.py`: node binding is limited to explicit local/private addresses; wildcard/public binds are rejected. `docs/ANDROID.md` documents the local firewall and phone setup.
- Wallet storage now passes the stored AES-GCM IV into decryption and has tamper/round-trip JVM tests. The manifest disables backup and supplies explicit backup/data-extraction exclusions.
- Android Compose wallet screens were redesigned and localized in Arabic/English. Chain balances and transfer history use RPC; the price chart does not fabricate prices. Transfer review rechecks nonce/balance/fee, signs locally, submits once, and verifies the returned transaction.
- `backend/`: the exchange now uses a persistent SQLite double-entry ledger and integer-unit order matching. Deposits/withdrawals are disabled, exchange routes are loopback-only, and no chain settlement or real-money custody is enabled.

## Phase 4 — closed test-only exchange (in progress)

- Current branch: `feat/kenan-exchange-ledger-2026-10-27`; no changes were merged into the default branch.
- The local Python suite passes **51/51** and Rust core tests pass **2/2**. GitHub Actions run [40605765704](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40605765704) passed its Python and Rust jobs; Kenan Runtime, Polkadot SDK and Android jobs were skipped by branch conditions.
- The exchange uses exact integer ledger units and rational limit prices; it does not assume KEN decimals or an asset policy. Fees remain unconfigured and are not charged. The book contains only submitted limit orders; no synthetic prices or liquidity are generated.
- Test-only fixture credits are callable only from automated tests. The backend service disables fixture funding, and deposit/withdrawal HTTP routes return unavailable without changing balances. This means a fresh service has no exchange balances; a chain-verified test-funding path remains an outstanding Phase 4 gate.
- The SQLite ledger, orders, trades and hash-linked audit are durable and checked on restart. Market buys require an explicit quote budget; open-order reservations and settlement are reconciled atomically.
- The exchange is local-only, not a production service. User identifiers are not authenticated identities, and no external security audit, backup/restore drill, asset registry or production fee/custody design is complete.

## Polkadot SDK baseline and Kenan Runtime — previous gates

- `polkadot/sdk/` is a Git submodule pinned to official release commit `cd87d0b95e54c8ffc374334386f3f2b2c7a61f54` (`polkadot-stable2603-7` / `polkadot-v1.22.7`). The unmodified official Solochain node built successfully with Rust `1.93.0`, locked dependencies and the `wasm32v1-none` target. The temporary 5.7 GB build tree was removed after testing; the pinned source remains in the repository.
- A real local development node served loopback RPC, produced/finalized blocks and accepted a signed Alice-to-Bob transfer of `1,000,000,000` base units. The recipient increased by the exact amount; the sender paid a calculated `759,936,145` base-unit fee; nonce changed `0 → 1`. Transaction hash: `0xd586064626c00169fdae429ed50e797ee899eb87a3aa14c3f91cbd707b61bc68`.
- The earlier Phase 1/2 and Android checks belong to their original branches and were not rerun for this backend-only Phase 4 change.
- The Kenan test-only Runtime overlay passed GitHub Actions run [40603492500](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40603492500). It verifies block production, finalized transfer, fee event, nonce and development Sudo boundaries; no economic or governance policy was approved.

## Not verified or not implemented yet

- **No Android emulator or physical phone was connected.** Installation, Android Keystore behavior on-device, LAN reachability from a phone, QR scanning, accessibility/small-screen behavior and a UI-to-live-node round trip remain to be tested on a real device.
- **Exchange completion gate:** no chain-verified test deposit, approved asset/unit mapping, authenticated exchange-user identity/session flow, backup/restore drill or independent security review is implemented. Deposits and withdrawals remain unavailable; no real funds are held.
- No release signing key, independent security audit or production operations review has been configured.

All balances and transactions are test data. Do not expose the node or faucet to untrusted networks or present this project as production financial infrastructure.
