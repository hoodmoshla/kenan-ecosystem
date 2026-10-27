# Kenan Ecosystem — Polkadot SDK, wallet and exchange roadmap

**Purpose:** build a separate, test-only Polkadot SDK chain and grow it into a compatible wallet and exchange integration without removing working Python devnet behavior. A plan, template, mock screen, or draft is not evidence that a feature works.

## Guardrails and current boundary

- `blockchain/` remains the current Python devnet. Keep it selectable and keep its tests green until the replacement has passed the applicable compatibility gates and a rollback path is verified.
- Continue wallet/RPC work and device testing alongside chain work. The previous phase passed Python 32/32, Rust 2/2, Android JVM 15/15, lint and CI; a real phone/emulator RPC round trip and on-device checks were not performed.
- The selected official SDK baseline is `polkadot-stable2603-7` (tag commit `cd87d0b95e54c8ffc374334386f3f2b2c7a61f54`), reported as tested with Rust Stable `1.93.0`. That exact source/toolchain/lockfile has now built successfully; source review alone is not proof. See [SDK source and compatibility notes](POLKADOT_SDK_RESEARCH.md) and the verified slice below.
- Keep local development/test chain keys and balances test-only. Never infer that existing Python phrases, addresses, signatures or balances map to Substrate accounts. Document address prefix/SS58, key derivation, signature type and SCALE encoding before wallet integration.
- [Tokenomics draft](TOKENOMICS_DRAFT.md) contains unapproved options only. No supply, decimals, allocation, fees, governance, validator policy or public genesis decision is approved by this roadmap. Do not launch a public testnet or represent test balances as having monetary value.
- Do not add real custody, real deposits/withdrawals, public registration, public trading, leverage, margin or derivatives during the initial exchange work.

## Phase 2 — Build a real local Kenan node with Polkadot SDK

Begin with an isolated workspace such as `polkadot/`; keep Python devnet, `wallet-core/`, `rust-core/` and Android behavior intact. The current SDK release’s [official Solochain template](https://docs.polkadot.com/parachains/customize-runtime/) is the starting point. Use the official template matching the pinned SDK release; do not silently substitute an older standalone template. Local development can use the template’s explicitly development-only genesis while tokenomics decisions remain open.

### Verified first slice (2026-10-27)

- `polkadot/sdk/` is pinned to official SDK tag `polkadot-stable2603-7` / `polkadot-v1.22.7`, commit `cd87d0b95e54c8ffc374334386f3f2b2c7a61f54`; the unmodified Solochain node built successfully with Rust `1.93.0` and `--locked --release`.
- A real local `Development` node produced/finalized blocks and served RPC on loopback. The integration smoke test signed an Alice-to-Bob transfer of `1,000,000,000` base units; the recipient increased by that exact amount, the sender paid a calculated `759,936,145` base-unit fee, and nonce changed `0 → 1`. Transaction hash: `0xd586064626c00169fdae429ed50e797ee899eb87a3aa14c3f91cbd707b61bc68`.
- Hosted [GitHub Actions run 40495611143](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40495611143) on [commit `e397fc7`](https://github.com/hoodmoshla/kenan-ecosystem/commit/e397fc7c442165a2d357020082a755478f11f00f) passed all four jobs: Python, Rust core, Android debug APK, and Polkadot SDK local-node integration/restart. The Polkadot job built the pinned SDK template and ran `./polkadot/test-local.sh` against the real local node.
- The integration program printed `result: passed` at finalized height `8`. Its checks passed for invalid and mutated signature rejection; unchanged balances/nonce for rejected transactions; replay and stale-nonce rejection; three sequential transfers with measured fees reconciled to sender/recipient balances; successful transfer events in finalized blocks; increasing finalized heights; and a 2-second delayed finality notification handled with one submission.
- The same CI log observed RPC outage while the node was stopped, restarted the node using the same chain data directory, and printed `PASS local node restart, RPC outage/recovery, persisted balances, nonce, and finalized extrinsics`. The test reads the template's existential deposit (`1,000,000,000`) and sends `1,500,000,000` to the initially unendowed test recipient so the transfer is valid.
- This verifies the upstream development template and its local-node gate only. It is not a Kenan-specific runtime/genesis or KenanWallet integration; the delayed-finality test exercises the integration client, not the Android wallet, and no phone/emulator RPC round trip was performed. Keep wallet integration and Kenan-specific monetary/authority decisions as later gates.

### What the pinned official template provides — and what Kenan still needs

The pinned upstream [Solochain runtime](../polkadot/sdk/templates/solochain/runtime/src/lib.rs) composes System, Timestamp, Aura, GRANDPA, Balances, Transaction Payment, Sudo and the example Template pallet. Its [runtime configuration](../polkadot/sdk/templates/solochain/runtime/src/configs/mod.rs) includes Substrate's generic SS58 prefix `42`, `AccountId32`-compatible multi-signature accounts, a `u128` balance type, a `u32` nonce, an existential-deposit constant, and weight/length-based transaction-fee machinery. The [chain spec](../polkadot/sdk/templates/solochain/node/src/chain_spec.rs) supplies Development and Local Testnet presets. These are upstream starter capabilities and development defaults, not approved Kenan currency settings.

The template does **not** define Kenan's approved asset name/precision, issuance or supply cap, distribution, fee destination, minimum-balance policy, governance, validator membership, production authority policy, Python-address migration, KenanWallet signing/metadata adapter, or exchange ledger. Sudo and development accounts are not a governance model or production key policy. A successful transfer on this template therefore proves SDK compatibility only; it does not make the chain a finished Kenan network. Any changes to monetary policy or privileged roles remain subject to the documented user decision gate.

### 2.1 Pin and build the baseline

- Record the exact upstream SDK/template revision, tested Rust version, required targets and native build prerequisites. Commit the toolchain and Cargo lockfile; builds must use locked dependencies.
- Create an isolated node/runtime workspace from the official Solochain template. Do not replace or rename the Python chain.
- Build from a clean checkout, start the actual node locally with development keys only, and verify the node is producing blocks and serving its real RPC. Do not call a draft, UI, mock or downloaded binary a built Kenan node.
- Keep genesis reproducible. Label the development spec and development accounts as test-only; do not encode an unapproved public distribution.

### 2.2 Runtime and node behavior

Use only the capabilities supported by the pinned template/release and keep changes minimal. Verify native accounts and balances, signed transfers, nonce validation and replay rejection, transparent test fees, block production/finality, RPC reads/submission/status, and persistence/restart. Aura and GRANDPA may be retained if they are present and compatible with the exact template. Do not add a custom pallet or infer currency policy without a documented requirement and approval.

### 2.3 Tests and CI gate

Add runtime/node tests and an actual local-node smoke test proving account/balance reads, valid transfer, invalid signature rejection, nonce/replay behavior, fee/balance accounting, block/finality progress, restart recovery and RPC response. Keep Python devnet tests, Rust core tests and Android tests/build in their existing CI jobs. Add a separate SDK CI lane only after local prerequisites and resource use are understood; pin its toolchain, build locked sources, run tests/smoke checks, and never cache or publish validator keys.

**Phase 2 completion gate:** a reproducible clean-checkout build; a real local SDK node starts, authors/finalizes blocks and accepts a real signed transfer; tests and RPC read-back pass; the Python suite remains green and the Python node still runs independently. Until all evidence exists, describe this phase as in progress, not complete. Do not proceed to the wallet integration phase on a failing gate.

## Phase 3 — Add Polkadot SDK as a separate KenanWallet network

Only after Phase 2 passes:

### Verified wallet integration slice (2026-10-27; hosted CI)

- Feature branch `feat/kenanwallet-polkadot-integration-2026-10-27`, code commit [`8206d054`](https://github.com/hoodmoshla/kenan-ecosystem/commit/8206d054a44f3c789a8cf8f7ffdd54ec47ba6105), passed [GitHub Actions run 40556600981](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40556600981): Rust core tests, Polkadot SDK local-node integration/restart recovery, Python tests, and Android debug APK.
- The Android CI step passed `:app:testDebugUnitTest`, `:app:lintDebug`, and `:app:assembleDebug`; its live transfer test against the local Solochain node finalized successfully and verified balance/fee/nonce reconciliation and no duplicate after a pre-submit connection failure.
- The `bad signature` root cause was the mortal-era checkpoint: the app used `height - phase`, while the pinned SDK defines `birth(current) = (max(current, phase) - phase) / period * period + phase`; for period 64, phase 22, the checkpoint is block 22, not genesis block 0. The [official SDK source at the pinned commit](https://github.com/paritytech/polkadot-sdk/blob/cd87d0b95e54c8ffc374334386f3f2b2c7a61f54/substrate/primitives/runtime/src/generic/era.rs) confirms the formula. No key derivation, Ed25519 signature format, V4 extrinsic version, or signed-extension order was changed.
- Android `BigInteger` narrowing now uses API-26-compatible round-trip checks while still throwing on overflow; boundary and overflow tests pass. CI artifacts retain the full Lint report if future Android checks fail.
- This is hosted CI evidence only: no physical-device/emulator RPC round trip was performed. The feature branch remains unmerged; this slice is not a release or security sign-off.

- Implement a separate Substrate RPC/client adapter; do not mix it with the Python devnet client or reuse Python transaction encodings.
- Add an explicit network selector for Python Devnet and Polkadot SDK Local/Testnet. Show the selected network and genesis hash before signing or submission.
- Use maintained, compatible libraries for SCALE metadata, extrinsic construction/signing and transaction fee, nonce and finality handling. Verify behavior against the pinned node, not assumptions about RPC names or encodings.
- Document Python `kenan1` versus SS58 addresses, key types, derivation paths and signature algorithms. Do not silently change existing account derivation or claim seed phrases generate the same account. Design and test an explicit migration if compatibility is possible; otherwise document the limitation.
- Exercise a wallet-signed transfer against the actual local node, then verify the finalized transaction and resulting balance. Test RPC outage, delayed finality and retry without duplicate submission.
- Keep Python as the default until all integration tests pass and rollback is verified. Preserve the existing Python, Rust and Android CI jobs and add a distinct SDK/wallet integration lane.

**Phase 3 completion gate:** wallet transfer and balance read-back succeed on a real local SDK node; wrong nonce/replay and network mismatch are rejected; RPC failure/delayed confirmation are handled honestly; Python remains selectable and unchanged.

## Requested Phase 3 — Kenan test-only Runtime overlay (2026-10-28)

- Feature branch `feat/kenan-runtime-customization-2026-10-27`, code commit [`7a6a3c3e`](https://github.com/hoodmoshla/kenan-ecosystem/commit/7a6a3c3e2bed48e6b39d0a012c52304b7c8aee15), passed [GitHub Actions run 40603492500](https://github.com/hoodmoshla/kenan-ecosystem/actions/runs/40603492500). Python and Rust core jobs also passed; the prior SDK-node and Android jobs were skipped by the phase-specific workflow conditions.
- The successful integration log verifies the test-only Kenan chain identity/runtime metadata and the `UNIT`/12 display properties, then reconciles genesis issuance exactly across the four upstream development-only accounts.
- Against the local loopback node, the test finalized an Alice-to-Bob transfer, checked the nonce increment, and matched the sender's balance delta to the finalized `TransactionFeePaid` event. The run observed `759,838,144` base units of fee; this is an ephemeral development-test result, not an approved fee schedule.
- The same run confirmed development Sudo rejects Bob and permits Alice only for an inert `System.remark` probe. The overlay inherits the official template's development genesis preset and keys; it does not establish a production authority or governance policy.
- This is a reproducible, test-only identity/runtime slice, not a custom economic Runtime. No issuance, supply, distribution, production fees, governance, or validator policy was approved or changed. The branch remains unmerged; physical-device testing and the closed exchange phase remain separate gates.

## Phase 4 — Build a closed, test-only Kenan exchange

Build functioning services, not decorative screens. Every screen must be connected to a real test service or clearly marked as unavailable/in development. Do not show fabricated balances, prices, liquidity or successful deposits/withdrawals.

### 4.1 Exchange services and interfaces

Plan explicit modules for market/balance dashboard, markets and pairs, spot trading, order book, trades/prices, user order history/status, asset and available/reserved balance management, deposits/withdrawals, internal ledger, notifications/audit events, account/session security, role-limited administration, and operational/liquidity/error reporting. Separate test data from any future production data.

### 4.2 Spot matching engine

Start with test-only spot `Market` and `Limit` buy/sell orders. Implement price-then-time priority, open/partially filled/filled/cancelled/rejected states, partial fills and cancellation of the remainder, configurable test fees, balance/asset validation, funds reservation before acceptance, release on cancellation/expiry, and unique/idempotent order and trade identifiers. Retries, concurrent submissions and disconnects must not create duplicate fills.

Add tests for order races, insufficient funds, cancellation, partial execution, repeated requests, idempotency and consistency between the book, trades, reservations and ledger.

### 4.3 Double-entry ledger and reconciliation

Use balanced immutable ledger entries with an audit trail; distinguish available and reserved amounts. Make reservation, release and settlement atomic where possible. Never edit a balance without a corresponding ledger entry. Use unique event identifiers, periodic balance/ledger reconciliation and explicit trade settlement. Test concurrency and fault paths for any possibility of creating funds. Exchange database balances are not blockchain balances; document the reconciliation boundary.

### 4.4 Demo deposits, withdrawals and market administration

- Deposits are test-only and auditable; credit only after verifying the actual test-chain transaction. Model pending/confirmed/rejected states and required confirmations.
- Withdrawals are request/review simulations only; validate balance, fee, limits and role approvals. Prevent duplicate processing. Do not create real withdrawals or hold user funds.
- Require an independently reviewed treasury-key management, multi-party approval, risk limits, monitoring and incident plan before any future real withdrawal design.
- Admin tools must audit every pair, status, limit and fee change. Define each asset and its rules before enabling a pair. Do not fabricate liquidity or random prices. Do not enable margin, leverage or derivatives in this phase.

### 4.5 Security and operation

Separate user/admin authentication and permissions; protect sessions; add rate limits, audit logs, secret management, backups with restore tests, health/error/transaction-lag monitoring, and concurrency/load/recovery tests. No default credentials or committed secrets. Keep the exchange closed and test-only; automated tests alone are not a security audit or legal approval.

**Phase 4 completion gate:** spot order and ledger invariants pass integration, concurrency, idempotency and recovery tests; every user-visible action is backed by a real test service or explicitly unavailable; no real-money operation is enabled.

### Phase 4 implementation status (2026-10-28)

- Work is isolated on `feat/kenan-exchange-ledger-2026-10-27`; the default branch and existing phase branches are unchanged.
- The in-memory float engine was replaced with a durable SQLite order/trade store and append-only, hash-linked double-entry journals. Every journal balances independently per asset; user available/reserved accounts cannot become negative. Startup/reconciliation checks ledger hashes, active-order reservations, trade fills and exact integer settlement.
- Amounts are integer ledger units and prices are rational `price_num/price_den` ratios. This is an internal test representation only; no KEN decimal mapping, asset registry, supply or fee policy is selected. Fees remain unconfigured and are not charged.
- Limit and market buy/sell matching uses price-time priority, exact integer settlement (fractional quote-unit fills are not rounded), partial-fill states, cancellation releases, and atomic idempotency records. Market buys require an explicit quote budget; market remainders do not rest.
- The backend persists to `data/exchange.sqlite3` (or `KENAN_EXCHANGE_DB`), binds Exchange routes to loopback callers, and records mutation/rejection events in an immutable hash-linked audit table. Admin status remains token-protected.
- `/exchange/deposit-demo`, `/exchange/deposit`, and `/exchange/withdraw` return unavailable; no exchange funds can be minted via HTTP. Test fixture credits exist only in automated tests, are balanced against a test-fixture clearing account, and are disabled in the backend service. Chain-verified deposits remain a future gate; exchange balances are not Python-devnet or Polkadot-chain balances.
- Local verification on this branch: `python3 -m unittest discover -s tests -v` — **51/51 passed**; `cargo test --locked --manifest-path rust-core/Cargo.toml` — **2/2 passed**. GitHub CI is pending.
- This is still a local test service, not a production exchange or a security audit. Phase 4 remains open until final branch CI and its integration/concurrency/recovery gates are verified.

## Phase 5 — Integrate exchange, SDK chain and wallet

Only after the chain, wallet adapter and exchange test services pass their separate gates:

- Put a defined service interface between exchange services and the SDK node. Read test-chain balances and observe real test transactions.
- Require configured confirmations before a deposit is credited. Link deposits/withdrawal requests and trade settlement to unique ledger events; reconcile chain and internal balances without conflating them.
- Test node/service restart, RPC outage, delayed transaction, duplicate request, order cancellation, reservation release and final settlement.
- Do not call the exchange decentralized merely because it connects to a blockchain; document which records/operations are on-chain and which are controlled by the centralized exchange service.

**Phase 5 completion gate:** end-to-end wallet, node, deposit-observer and ledger tests pass on the actual local/test network, including duplicate prevention and recovery; no real custody or public trading is enabled.

## Phase 6 — Authorized private network, before any launch decision

After local chain and integration gates pass, run a private testnet starting with one internal validator, then multiple independent validator processes/machines as needed. Test finality, restart, backup/restore, configuration changes, runtime upgrade, key rotation, monitoring and incident response. Protect validator keys; never put them in Git, CI logs or APKs. Run the wallet and closed exchange end-to-end on this network.

Before even considering production, require an independent security review, operational and incident-response readiness, approved genesis/tokenomics, legal review appropriate to the intended jurisdictions, and a tested rollback/migration path. Green automated CI alone is not evidence of financial safety. No phase in this plan authorizes mainnet, real funds, public registration or a public exchange listing.

## Delivery practice

- Keep changes small, reviewable and phase-gated; preserve existing working behavior and tests.
- Run the relevant tests and CI for Python, Rust, Android, SDK and exchange services for each change. Record actual commands and results; distinguish a local check from hosted GitHub CI.
- Do not push or publish a network, enable production settings, or claim a feature passed without evidence. At each phase boundary, report changes, commit, tests, limitations and the next gate.
- If a phase is too large, implement and verify its first buildable slice before expanding scope; do not substitute another plan document or UI for running code.
