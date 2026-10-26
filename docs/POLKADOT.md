# Kenan on Polkadot SDK — staged implementation plan

**Status: planning only.** This repository has no Polkadot SDK node or runtime. The Python chain in `blockchain/` remains the current working test model and must remain available until the migration gates below pass. Do not describe the plan as a running Substrate network.

## 1. Decide the chain policy first (about 2–5 working days)

Document and approve the testnet token issuance/distribution, symbol `KEN`, 9 decimals, existential deposit, transaction fee model, governance/upgrade authority and validator roles. `docs/TOKENOMICS_DRAFT.md` is not an approved policy. Confirm whether a custom metadata pallet is really needed; standard Balances properties may suffice.

**Gate A:** written decisions, test-only status, and agreement on a reversible migration. If decisions are open, do not encode them in a public genesis spec.

## 2. Build a separate solo-chain node (roughly 1–2 weeks for an experienced Rust team)

At implementation time, re-check the official `paritytech/polkadot-sdk` releases, the official node-template guidance and system requirements. The version string previously discussed in this repository is only a planning lead; do not pin it from memory. Choose an official supported release/commit, record the toolchain and lockfiles, and create an isolated workspace such as `polkadot/`. Do not replace `blockchain/` or the `rust-core/` library.

Start from the official solo-chain/node template for the chosen SDK. Build a local single-validator dev chain first. Add only the agreed runtime configuration and required pallets (for example System, Timestamp, Aura, GRANDPA, Balances and TransactionPayment). Keep `Sudo` dev-only and remove it before any public testnet. Do not add a custom pallet without a specified requirement.

**Evidence:** clean-checkout reproducible build; local node starts, authors and finalizes blocks; two test accounts can transfer the test asset; runtime/unit tests verify fee, nonce and issuance rules.

## 3. Add a separate CI lane before expensive builds

Keep existing Python, Rust-core and Android jobs intact. Add an isolated SDK lane on a runner with the memory/disk requirements of the selected release (the earlier 16-GB estimate must be verified against that release). Fail fast with `cargo fmt --check` and `cargo check --locked`; then run runtime/node tests and a local smoke test; only after those pass should CI build the Wasm runtime and release node. Cache only pinned Rust/target artifacts, never validator keys. Record the SDK release, Rust toolchain, chain-spec hash and build artifacts.

**Gate B:** CI passes from a clean checkout and the Python devnet and Android Debug build remain unchanged and green.

## 4. Write an explicit adapter and integration tests (roughly 2–4 weeks)

Keep the current Python HTTP RPC and add a separate Substrate JSON-RPC client/adapter. Verify method names and response encodings against the selected SDK version rather than assuming they match the tentative mapping below. Implement SCALE extrinsic construction/signing through a maintained compatible library; never send the Python `CHAIN_ID|from|to|...` message as a Substrate payload.

Test account nonce, fee estimation (`payment_queryInfo`), signed transfer, finality, replay rejection, wrong nonce, balance read-back and transaction lookup. Give the user an explicit network choice and show network identity/genesis hash. Do not switch the default or silently reinterpret old addresses.

**Gate C:** a wallet transfer is finalized and read back with the expected account/nonce/fee; replay/wrong-nonce cases fail; Android and backend integration tests pass against the real local SDK node; Python devnet still works independently and remains selectable.

## 5. Run an authorized private testnet before changing defaults (roughly 1–3 further weeks)

Start with one internal validator, then exercise 2–3 independent validator processes/machines. Generate and protect each validator key per operational procedure; test restart, finality, backup/restore, alerting, key rotation and runtime upgrade/recovery. Do not put keys in a chain spec, Git, CI logs or Android artifacts.

Before making the SDK network the default for any user build, document a rollback path, complete the integration gates, obtain an independent security/operations review, approve the genesis distribution, and show the Python devnet still works unchanged. This gate does not authorize real funds, a public listing or mainnet.

## Compatibility and migration warnings

- Current Python addresses are `kenan1` plus the first 20 bytes of SHA-256(public key); they are not SS58 addresses. Existing `kenan1` balances do not automatically migrate. Re-derive or re-issue accounts only under an approved, transparent testnet policy.
- Do not assume the same phrase maps to the same account if key-derivation path, key type or signing scheme differs. Test SS58 prefix/checksum and wallet derivation with official SDK-compatible libraries.
- Identify the network by chain identity/genesis hash, not address prefix alone. Keep Python and SDK networks distinct in UI and API until an explicit opt-in migration is available.
- Python devnet balances have no monetary value and must not be presented as transferred value. Tokenomics, validator rewards, fees, existential deposit and distribution require review before genesis.

## Rough effort and proof required

A private, repeatable SDK testnet is roughly **6–14 weeks of experienced engineering effort**, excluding independent audit and production launch, plus build/CI wait time. The estimate depends on approved economics, SDK version and team size. Required evidence includes pinned SDK/toolchain, green CI, node/runtime build logs, unit and integration tests, chain-spec/genesis hash, validator restart/recovery results, and a wallet/backend test against the actual node. Existing Python tests or an Android APK alone are not proof of Polkadot compatibility.
