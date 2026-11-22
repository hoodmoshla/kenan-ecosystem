# Polkadot SDK source and compatibility notes

This note records the official sources used to select a local test-only SDK baseline. It is not an approved tokenomics or network-launch decision.

## SDK release selected for evaluation

- The latest final stable release visible on the official release page during this work is `polkadot-stable2603-7`, based on the `stable2603` release line and also tagged `polkadot-v1.22.7`: [official release page](https://github.com/paritytech/polkadot-sdk/releases/tag/polkadot-stable2603-7).
- That release reports Rust Stable `1.93.0` as the toolchain used to build and test its crates and node binaries. This is a tested toolchain, not a guarantee that another compiler version will work.
- The `polkadot-stable2609-rc1` and `rc2` entries on the [official releases page](https://github.com/paritytech/polkadot-sdk/releases) are explicitly pre-releases. They are not selected as the default evaluation baseline.
- The official SDK tag resolved to commit `cd87d0b95e54c8ffc374334386f3f2b2c7a61f54` when checked. The sandbox default was Rust `1.97.1`, so Rust `1.93.0` was installed explicitly and used for the successful locked release build recorded below.

## Official template and workspace facts

- Polkadot Developer Docs list a Solochain template specifically for sovereign standalone chains: [Customize Your Runtime](https://docs.polkadot.com/parachains/customize-runtime/).
- The official stable SDK tag contains `templates/solochain/`, including the runtime/node template and configurations for Aura, GRANDPA, Balances, Timestamp, Transaction Payment and development-only Sudo: [template runtime manifest](https://raw.githubusercontent.com/paritytech/polkadot-sdk/polkadot-stable2603-7/templates/solochain/runtime/Cargo.toml), [development chain specification](https://raw.githubusercontent.com/paritytech/polkadot-sdk/polkadot-stable2603-7/templates/solochain/node/src/chain_spec.rs).
- In the SDK monorepo, template manifests inherit dependencies from the SDK root workspace; the `templates/solochain/` directory itself does not contain its own root `Cargo.toml`. A Kenan workspace must preserve/pin that dependency source or use a generated standalone template with a verified compatible release.
- The separate [official standalone Solochain template](https://github.com/paritytech/polkadot-sdk-solochain-template) documents `cargo build --release` and `--dev` operation. The repository has an open [issue about keeping up with new SDK versions](https://github.com/paritytech/polkadot-sdk-solochain-template/issues/32), so its current head should not be assumed compatible with stable2603 without checking its pinned dependencies.

## Build environment

- The official [SDK installation guide](https://docs.polkadot.com/parachains/install-polkadot-sdk/) describes Linux compiler/linker and OpenSSL prerequisites. Its hardware guidance recommends 16 GB RAM and at least 10 GB available storage for development; current workspace resources are finite and must be checked before a full node build.
- The actual build ran on Ubuntu 24.04.4 with 23 GiB RAM and Rust `1.93.0`; its locked release build completed successfully in 26m 21s. In this environment the build needed `libclang-dev`, `llvm-dev`, `cmake`, and `protobuf-compiler` in addition to C/C++ build tools and OpenSSL development files. The temporary source/build tree reached 5.7 GiB; after the real-node smoke test it was deleted, leaving the pinned 236 MiB submodule source in the repository and restoring 7.1 GiB free. Disk space was briefly down to 1.4 GiB, so check capacity before repeating a full build.

## Policy boundary

The SDK template's built-in development genesis is test-only. Token supply/distribution, fee policy, existential deposit, validator authority and public-testnet settings remain unapproved. Do not encode a production or public genesis from the draft; the Python devnet remains unchanged and available until explicit migration gates pass.
