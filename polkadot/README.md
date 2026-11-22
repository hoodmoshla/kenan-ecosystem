# Polkadot SDK local-node baseline

This directory keeps the first SDK node isolated from the working Python devnet. `sdk/` is a Git submodule pinned by the parent repository to the official Polkadot SDK commit `cd87d0b95e54c8ffc374334386f3f2b2c7a61f54` (`polkadot-stable2603-7` / `polkadot-v1.22.7`). The upstream release reports Rust Stable `1.93.0` as its tested compiler. The upstream `Cargo.lock` is used with `--locked`.

**Scope:** this is an unmodified official Solochain template baseline, not yet a Kenan-specific runtime or approved currency. Its development chain uses development-only accounts and is for local testing only. It does not migrate Python addresses, keys, balances or transactions. Do not expose its dev RPC to an untrusted network, use development keys on a public chain, or describe template balances as having monetary value. Token policy remains unapproved; see [the draft](../docs/TOKENOMICS_DRAFT.md) and the [roadmap](../docs/POLKADOT.md).

## Rebuild

From the repository root, initialize the pinned source and install the tested toolchain/target:

```sh
git submodule update --init --depth 1
rustup toolchain install 1.93.0 --profile minimal --component rustfmt
rustup target add wasm32v1-none --toolchain 1.93.0
./polkadot/build-node.sh
```

The build uses the template at `sdk/templates/solochain/node/` and emits `sdk/target/release/solochain-template-node`. On Ubuntu, install missing native prerequisites with `sudo apt-get install build-essential cmake libclang-dev llvm-dev libssl-dev pkg-config protobuf-compiler`. The pinned SDK and its native dependencies require substantial disk space; consult the official [installation guide](https://docs.polkadot.com/parachains/install-polkadot-sdk/) for platform-specific requirements.

## Run locally

```sh
./polkadot/run-local.sh
```

The wrapper starts the template with `--dev`, sets the RPC port from `KENAN_RPC_PORT` (default `9944`), and explicitly binds P2P to IPv4 loopback using `KENAN_P2P_PORT` (default `30333`). It does not forward arbitrary node flags that could expose RPC or P2P. This wrapper is for one-machine development only; do not use it as a multi-machine private-testnet launcher.

The baseline is only the first buildable slice; its real signed-transfer smoke test passes. A Kenan-specific runtime/genesis, replay and invalid-signature tests, restart recovery, wallet outage/delayed-finality integration and hosted CI result are still required before Phase 2 can be called complete. The Python devnet remains available and unchanged, and no token-economics decision is approved.

## Verify an actual local transfer

Install the pinned test client, build the node, then run the temporary local-chain smoke test:

```sh
npm ci --prefix polkadot/integration-test
./polkadot/build-node.sh
./polkadot/test-local.sh
```

The test starts a real local node with RPC and P2P restricted to loopback, waits for a finalized block through RPC, signs a transfer locally with the template's development-only Alice key, sends it to the development-only Bob account, and verifies the finalized event, recipient balance, sender fee deduction and nonce increment. It refuses a non-loopback WebSocket endpoint. The temporary chain database is removed when the test exits. This does not test the KenanWallet Android app or authorize reusing these development keys outside the local test.
