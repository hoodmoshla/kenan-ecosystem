# Kenan Ecosystem — Status: what is verified vs. what is pending

Last verified in this environment: Python 3.12, Node 24, Rust 1.99.1 (installed
during this build). No Java / Android SDK in this VM.

## ✅ Verified by automated tests (13/13 passing, `python3 -m unittest discover -s tests`)
- Devnet chain: Ed25519-signed transfers, nonce + replay protection, flat
  fee (0.001 KEN, 50% burned / 50% fee-collector), hash-chained blocks,
  persistent state, balance/tx lookup over HTTP RPC.
- Wallet: 24-word BIP39 creation, restore, backup-proof check, sign/verify,
  tamper detection. Keys never leave the signing process.
- Exchange (DEMO): limit matching, partial fills, reserved balances,
  cancel releasing reservations, insufficient-balance rejection.
- Modules: Shield levels, Intelligence key-refusal + unsigned drafts,
  Circles caps/thresholds, Pay receipts requiring a tx hash, Academy
  virtual accounts, Insights PnL-unavailable without a price source,
  Governance signalling (non-binding), Recovery timelock, Bridge DISABLED.
- HTTP integration: node RPC transfer end-to-end + backend live-balance proxy.

## ✅ Also verified in this environment
- Rust core (`rust-core/`): `cargo test --release` 2/2 passing
  (Ed25519 sign/verify roundtrip + tamper rejection, fee rule), Rust
  1.99.1, crates ed25519-dalek 2.2.0 / sha2 / hex. Cross-check verified:
  a Rust signature for a fixed test seed verifies in Python
  (`rust-core/examples/interop.rs`) and the derived address matches.

## 🟡 Built, not yet compiled in this environment
- Android app (`android-app/`): Kotlin/Compose RTL source, navigation,
  loading/error/empty states, live-balance client. NOT compiled — no
  Android SDK/Java here, so NO APK is claimed or delivered. Building an
  APK requires Android Studio / SDK + JDK 17 (see docs/ANDROID.md).

## ❌ Explicitly NOT done / NOT claimed
- Polkadot SDK / Substrate node: NOT built. A full Substrate node needs
  far more RAM/disk/time than this 2-CPU VM offers; the Python devnet is
  the testable stand-in, with the same sign/balance/submit boundaries.
- Mainnet, real money, public KEN listing, real trading, custody of user
  funds: NONE. Everything here is devnet/demo and labelled as such.
- LLM provider for Intelligence: not connected; rule-based explainer only.
- Bridge: code refuses all transfers until an independent audit exists.
- Protocol-level governance, on-chain DEX, recurring payments: design /
  signalling only.
- External services still needed before any public launch: see
  docs/OPERATIONS.md (PostgreSQL, Redis if justified, secrets manager,
  price feed with source+timestamp, independent security audit, legal /
  licensing review, Substrate runtime + testnet validators).
