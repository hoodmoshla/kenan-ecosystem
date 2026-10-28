# Kenan Ecosystem — verified status (2026-10-28)

This is a test-only project status, not a production-readiness or financial-safety claim.

## Current branch and verification

Work remains on `feat/kenan-exchange-ledger-2026-10-27`, commit [`f4c4256`](https://github.com/hoodmoshla/kenan-ecosystem/commit/f4c4256bd4e3635d53853971f3e8749f4ca3f9ae). It has not been merged into `main`, and repository visibility was not changed.

Locally, `python3 -m unittest discover -s tests -v` passed **67/67**, covering account/session handling, ownership isolation, DEMO order execution and recovery, ledger invariants, and observer rejection/replay cases. `cargo test --locked --manifest-path rust-core/Cargo.toml` passed **2/2**; Shell, Node.js and Python syntax checks and `git diff --check` also passed.

Hosted [GitHub Actions run 40681848766](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40681848766) on this commit completed successfully. Python, Rust, Kenan Runtime integration, official Polkadot SDK local-node/restart recovery, and Android debug APK jobs all passed; none were skipped on this feature branch. The Kenan Runtime job produced a finalized test transfer, handed it to the exchange observer as `pending_settlement_review`, verified zero DEMO ledger credit, then replayed after service restart without duplication. That runtime exposes `UNIT/12`, **not** an approved KEN asset or precision. The official upstream Solochain node also passed signed-transaction, finality, RPC outage and restart tests.

The Android job passed **31/31 JVM tests with 0 skipped**, including its live Substrate integration against the **official upstream local template**; `lintDebug` reported **0 errors and 8 warnings**, and `assembleDebug` succeeded. CI uploaded `KenanWallet-debug-and-test-reports`; the extracted Debug APK is **18,728,454 bytes**, SHA-256 `b8e39c3574d7246d77f98dc75bcb7e928141fef1eee5883efa0bbf21c8d9f18c`. It is not release-signed. The build log also emitted Kotlin metadata compatibility diagnostics during analysis, plus upstream GitHub Action/Node deprecation warnings; review these before release. This is not a physical-phone test or a Kenan Runtime Android test.

## What changed

Exchange now has local test accounts and bearer sessions: passwords use salted PBKDF2-HMAC-SHA256, raw session tokens are not stored, sessions expire after eight hours and can be revoked, and private reads/mutations are tied to the authenticated account. Registration creates only a `trader`; legacy unauthenticated IDs are reserved rather than claimable. These are loopback-only safeguards, not production identity; MFA, rate limiting, password recovery, public TLS and independent security review are absent.

`backend/exchange/chain_deposits.py` enforces pinned network/runtime/asset identity, finalized sequential blocks, extrinsic-hash and call/event agreement, signed origin, exact amount, raw AccountId32 beneficiary binding, and idempotent checkpoints. It persists observations for review only and has no trading-balance credit operation. The Node/Python harness tested this boundary against an actual finalized transfer in the ephemeral Kenan Runtime; it is not a continuously running RPC watcher or settlement service.

A new `polkadot/kenan-runtime/run-local.sh` launches the persistent test-only Runtime with RPC and P2P loopback-only, reverses its temporary SDK overlay on shutdown, and stores chain data in a Git-ignored path. `docs/ANDROID.md` describes Wireless ADB with `adb reverse`, so a phone can reach local RPC without opening it on Wi-Fi/LAN. DEMO funding caps and the order book remain unchanged and separate from Polkadot; deposits and withdrawals still return unavailable.

## What remains unverified or disabled

No Android phone or emulator was connected in this sandbox; `adb` and a locally built Polkadot node were unavailable, and disk space was insufficient for the large SDK build. Therefore no phone/Wi-Fi, on-device Keystore, QR, accessibility, small-screen or UI-to-live-node result is claimed. The CI APK can be installed and tested using the steps in `docs/ANDROID.md`, but this still requires a computer and physical device.

There is no approved KEN asset/precision mapping, KEN deposit credit, withdrawal or settlement-approval path. The test Runtime's `UNIT/12` must not be treated as KEN. There is also no continuous production-grade chain watcher, backup/restore drill, independent security audit, release-signing key, or production/legal operations review. No real funds, public trading or custody are enabled.

Prior SDK and wallet results documented in earlier status reports apply only to their earlier branch/commit; the checks above specify what was actually rerun for this follow-up.
