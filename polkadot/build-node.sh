#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="$SCRIPT_DIR/sdk"
MANIFEST="$SDK_DIR/templates/solochain/node/Cargo.toml"

if [[ ! -f "$MANIFEST" || ! -f "$SDK_DIR/Cargo.lock" ]]; then
  echo "Pinned Polkadot SDK source is missing. Run: git submodule update --init --depth 1" >&2
  exit 2
fi
if ! rustc +1.93.0 --version >/dev/null 2>&1; then
  echo "Rust 1.93.0 is required; install it with rustup toolchain install 1.93.0 --profile minimal" >&2
  exit 2
fi
if ! rustup target list --toolchain 1.93.0 --installed | grep -Fxq wasm32v1-none; then
  echo "The wasm32v1-none target is required; install it with rustup target add wasm32v1-none --toolchain 1.93.0" >&2
  exit 2
fi

# Bound memory/disk use by default; CI or developers may override these values.
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-2}"
export CARGO_INCREMENTAL="${CARGO_INCREMENTAL:-0}"
export CARGO_PROFILE_RELEASE_DEBUG="${CARGO_PROFILE_RELEASE_DEBUG:-0}"
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS="${CARGO_PROFILE_RELEASE_CODEGEN_UNITS:-16}"

cargo +1.93.0 build --locked --release --manifest-path "$MANIFEST"
