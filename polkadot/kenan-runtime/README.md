# Kenan Runtime overlay — local test only

This directory contains a small, reviewable overlay on the official Solochain template pinned at Polkadot SDK commit [`cd87d0b95e54c8ffc374334386f3f2b2c7a61f54`](https://github.com/paritytech/polkadot-sdk/tree/cd87d0b95e54c8ffc374334386f3f2b2c7a61f54/templates/solochain). The upstream Git submodule is left unchanged. `apply-overlay.py` changes only the runtime identity and adds a separate `kenan-dev` chain spec; `test-runtime.sh` applies those exact replacements temporarily, builds and runs the node on loopback, executes the integration tests, and reverses the replacements on exit.

The chain name and runtime identity are explicitly test-only. Its genesis, development keys, `UNIT` symbol, 12 decimal display property, existential deposit, fee calculation, and Alice Sudo authority are inherited from the official template. The development preset currently endows its fixed test accounts. These are upstream test fixtures—not Kenan issuance, currency, fee, validator, governance, or permission decisions. No change here authorizes a public network. Open decisions and risks remain in [the tokenomics draft](../../docs/TOKENOMICS_DRAFT.md).

## Run

Install the pinned JavaScript RPC client and the SDK's tested Rust toolchain/target, then run:

```sh
npm ci --prefix polkadot/integration-test
rustup toolchain install 1.93.0 --profile minimal
rustup target add wasm32v1-none --toolchain 1.93.0
./polkadot/kenan-runtime/test-runtime.sh
```

The script refuses a different SDK commit or a dirty submodule. It binds the node's RPC and P2P ports to IPv4 loopback; the default RPC port is `9950`. Override it with `KENAN_RUNTIME_RPC_PORT` only if that local port is occupied. The SDK build is large and should run in CI or a machine with adequate free disk space.

## Live checks

`test-kenan-runtime.cjs` connects to the built node and checks the actual runtime/chain identity and explicit chain properties, that genesis `totalIssuance` exactly reconciles to the four endowed development-only accounts, a signed transfer's finalized recipient/sender balances and nonce, a positive runtime fee, and that the configured Sudo account—not Bob—can invoke Sudo (the permitted probe is only an inert `System.remark`).

The tests deliberately do not approve an issuance schedule, real currency symbol/precision, fee destination/rate, existential deposit, production root authority, validator set, or upgrade/governance process. Development private keys and this chain spec must never be reused outside an isolated local test.
