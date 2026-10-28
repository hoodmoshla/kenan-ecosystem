# Kenan Ecosystem — verified status (2026-10-28)

This status records the current test-only project state. It is not a production-readiness or financial-safety claim.

## Verified checks and artifact

- **Python:** `python3 -m unittest discover -s tests -v` — **54/54 passed** on the current Phase 4 worktree, including Devnet regression checks plus capped local funding, double-entry accounting, partial fills, idempotency, concurrency, rollback, restart recovery and HTTP restrictions.
- **Rust:** `cargo test --locked --manifest-path rust-core/Cargo.toml` — **2/2 passed**. `rust-core/` remains a small signing/address library, not a blockchain node.
- **Android:** `:app:testDebugUnitTest` — **31 tests, 0 failures, 1 skipped** (the live-node integration test is assumption-skipped because no RPC URL was supplied); `:app:lintDebug` — **0 errors, 11 warnings**; `:app:assembleDebug` — passed. Warnings are existing target-SDK/dependency/pluralization/shared-preference advisories; no dependency upgrade was made as part of the exchange task.
- **Debug APK:** `android-app/app/build/outputs/apk/debug/app-debug.apk`; package `com.kenan.wallet`, **18,903,366 bytes**, SHA-256 `f2221179417de55afc82c9b47421f80981d96dd8f2dc364997a1bf12e58ecebf`. It is signed with the standard Android Debug certificate, not a project release key.
- **Earlier CI baseline:** run [40605765704](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40605765704) passed Python and Rust for the previous Exchange commit; Kenan Runtime, Polkadot SDK and Android jobs were skipped by branch conditions. Current follow-up CI is reported separately below after it runs.

## Implemented in this change — development only

- `android-app/`: explicit RPC endpoint resolution. The emulator may use `10.0.2.2`; a physical phone gets no guessed LAN IP and must be configured with the computer's private Wi-Fi IP and port `8545`. UI distinguishes unconfigured, unreachable and empty-history states. No API is exposed to a public address by default.
- `blockchain/node_server.py` and `network_utils.py`: node binding is limited to explicit local/private addresses; wildcard/public binds are rejected. `docs/ANDROID.md` documents the local firewall and phone setup.
- Wallet storage now passes the stored AES-GCM IV into decryption and has tamper/round-trip JVM tests. The manifest disables backup and supplies explicit backup/data-extraction exclusions.
- Android Compose wallet screens were redesigned and localized in Arabic/English. Chain balances and transfer history use RPC; the price chart does not fabricate prices. Transfer review rechecks nonce/balance/fee, signs locally, submits once, and verifies the returned transaction.
- `backend/`: the exchange now uses a persistent SQLite double-entry ledger and integer-unit order matching. Deposits/withdrawals are disabled, exchange routes are loopback-only, and no chain settlement or real-money custody is enabled.

## Phase 4 — closed test-only exchange (local DEMO cycle implemented; broader gates remain)

- Current branch: `feat/kenan-exchange-ledger-2026-10-27`; no changes were merged into the default branch.
- The Python HTTP service and local CLI share only the dedicated `data/demo-exchange.sqlite3` ledger. `python3 -m backend.exchange.demo_funding grant` is not imported by the HTTP app or Android app; it issues only `DEMO_BASE`/`DEMO_QUOTE` opaque units, at most one grant per account/asset and 10,000 units per grant, with a global cap of 100,000 raw units across both assets. A repeated grant ID is idempotent; every accepted grant posts a double-entry journal and immutable hash-linked audit event.
- All exchange accounts, assets, balances, orders, fills, funding records, journals and responses carry `DEMO`/`test_only` identity. The ledger derives balances from immutable integer entries, rejects direct row mutation, checks reservations and reconciles trades on startup. The database is separate from Python devnet and Polkadot state; DEMO units are not KEN or a monetary amount.
- Limit and market orders support price-time matching, full/partial execution, reservation, cancellation release, integer/rational math and request idempotency. The visible book contains submitted limit orders only. No price feed or synthetic liquidity is generated. HTTP fees default to zero; fee arithmetic is tested only with a test-configured engine and is not an approved policy.
- HTTP deposits/withdrawals and funding routes are unavailable; the only funding command runs locally. Exchange HTTP callers are loopback-only; the Android app has no funding button and a separate phone/emulator cannot access the local exchange service. `/admin/status` remains token-protected.
- **Local checks:** Python **54/54**, Rust **2/2**, Android JVM **31 tests / 0 failures / 1 live-node test skipped**, Android lint **0 errors / 11 existing warnings**, and Debug APK build passed. GitHub Actions for this follow-up is still to be reported after the branch push.
- The local DEMO trading cycle is not production readiness. Exchange user IDs are not authenticated identities, and no chain-verified test deposit, approved asset/unit mapping, backup/restore drill, independent security review or real-money custody exists. No deposit, withdrawal or real trading is enabled.

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
