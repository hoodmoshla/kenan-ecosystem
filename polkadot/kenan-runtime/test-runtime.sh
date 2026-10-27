#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SDK="$ROOT/polkadot/sdk"
OVERLAY="$ROOT/polkadot/kenan-runtime/apply-overlay.py"
EXPECTED_SDK_REV="cd87d0b95e54c8ffc374334386f3f2b2c7a61f54"
RPC_PORT="${KENAN_RUNTIME_RPC_PORT:-9950}"
P2P_PORT="${KENAN_RUNTIME_P2P_PORT:-30335}"
NODE_PID=""
PATCH_APPLIED=0
BASE_DIR=""
NODE_LOG=""

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "$NODE_PID" ]]; then
    kill "$NODE_PID" 2>/dev/null || true
    wait "$NODE_PID" 2>/dev/null || true
  fi
  if (( PATCH_APPLIED )); then
    python3 "$OVERLAY" revert || {
      echo "Failed to reverse the Kenan Runtime overlay; inspect $SDK before continuing." >&2
      status=1
    }
  fi
  if [[ -n "$BASE_DIR" && -d "$BASE_DIR" ]]; then
    if [[ "$status" -ne 0 && -n "$NODE_LOG" && -f "$NODE_LOG" ]]; then
      echo '--- local Kenan node log ---' >&2
      cat "$NODE_LOG" >&2
    fi
    rm -rf -- "$BASE_DIR"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

actual_rev="$(git -C "$SDK" rev-parse HEAD)"
if [[ "$actual_rev" != "$EXPECTED_SDK_REV" ]]; then
  echo "Expected official Polkadot SDK $EXPECTED_SDK_REV; found $actual_rev. Refusing to apply the overlay." >&2
  exit 2
fi
if [[ -n "$(git -C "$SDK" status --porcelain)" ]]; then
  echo 'The Polkadot SDK submodule has local changes; refusing to overwrite or reverse them.' >&2
  exit 2
fi
if [[ ! -d "$ROOT/polkadot/integration-test/node_modules/@polkadot/api" ]]; then
  echo 'Install the pinned integration-test dependencies first: npm ci --prefix polkadot/integration-test' >&2
  exit 2
fi

# The overlay is version-controlled outside the upstream submodule, applied only for this build/test,
# and reversed on exit. No SDK source commit or upstream tag is changed.
python3 "$OVERLAY" apply
PATCH_APPLIED=1

export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"
export CARGO_INCREMENTAL="${CARGO_INCREMENTAL:-0}"
export CARGO_PROFILE_RELEASE_DEBUG="${CARGO_PROFILE_RELEASE_DEBUG:-0}"
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS="${CARGO_PROFILE_RELEASE_CODEGEN_UNITS:-16}"

if [[ "${KENAN_RUNTIME_SKIP_BUILD:-0}" == "1" || "${KENAN_RUNTIME_SKIP_BUILD:-0}" == "true" ]]; then
  printf 'Reusing the exact cached Kenan test node for SDK %s.\n' "$EXPECTED_SDK_REV"
else
  printf 'Building Kenan test runtime from pinned SDK %s...\n' "$EXPECTED_SDK_REV"
  cargo +1.93.0 build --locked --release --manifest-path "$SDK/templates/solochain/node/Cargo.toml"
fi

NODE_BIN="$SDK/target/release/solochain-template-node"
if [[ ! -x "$NODE_BIN" ]]; then
  echo "Expected node binary not found: $NODE_BIN" >&2
  exit 1
fi
if [[ "${KENAN_RUNTIME_BUILD_ONLY:-0}" == "1" ]]; then
  printf 'Kenan test node build is ready: %s\n' "$NODE_BIN"
  exit 0
fi
BASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/kenan-runtime-test.XXXXXX")"
NODE_LOG="$BASE_DIR/node.log"
"$NODE_BIN" --chain kenan-dev --base-path "$BASE_DIR/db" \
  --rpc-port "$RPC_PORT" --rpc-methods unsafe \
  --listen-addr "/ip4/127.0.0.1/tcp/$P2P_PORT" >"$NODE_LOG" 2>&1 &
NODE_PID=$!

ready=0
for _ in $(seq 1 90); do
  if ! kill -0 "$NODE_PID" 2>/dev/null; then
    echo 'Kenan test node exited before RPC became ready.' >&2
    exit 1
  fi
  if curl --silent --show-error --fail --max-time 2 \
    -H 'Content-Type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"system_chain","params":[]}' \
    "http://127.0.0.1:$RPC_PORT/" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "$ready" -ne 1 ]]; then
  echo 'Timed out waiting for the loopback Kenan test node RPC.' >&2
  exit 1
fi

export KENAN_RUNTIME_RPC_WS="ws://127.0.0.1:$RPC_PORT"
export NODE_PATH="$ROOT/polkadot/integration-test/node_modules${NODE_PATH:+:$NODE_PATH}"
node "$ROOT/polkadot/kenan-runtime/test-kenan-runtime.cjs"
printf 'Kenan Runtime integration checks passed on %s.\n' "$KENAN_RUNTIME_RPC_WS"
