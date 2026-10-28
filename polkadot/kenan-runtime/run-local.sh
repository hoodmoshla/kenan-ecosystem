#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
SDK="$ROOT/polkadot/sdk"
OVERLAY="$ROOT/polkadot/kenan-runtime/apply-overlay.py"
EXPECTED_SDK_REV="cd87d0b95e54c8ffc374334386f3f2b2c7a61f54"
RPC_PORT="${KENAN_RUNTIME_RPC_PORT:-9950}"
P2P_PORT="${KENAN_RUNTIME_P2P_PORT:-30335}"
BASE_PATH="${KENAN_RUNTIME_BASE_PATH:-$ROOT/data/kenan-polkadot-dev}"
NODE_PID=""
PATCH_APPLIED=0

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [[ -n "$NODE_PID" ]]; then
    kill -TERM "$NODE_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do
      if ! kill -0 "$NODE_PID" 2>/dev/null; then break; fi
      sleep 1
    done
    kill -KILL "$NODE_PID" 2>/dev/null || true
    wait "$NODE_PID" 2>/dev/null || true
  fi
  if (( PATCH_APPLIED )); then
    python3 "$OVERLAY" revert || {
      echo "Failed to reverse the Kenan Runtime overlay; inspect $SDK before continuing." >&2
      status=1
    }
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
if ! rustc +1.93.0 --version >/dev/null 2>&1; then
  echo 'Rust 1.93.0 is required; install it with rustup toolchain install 1.93.0 --profile minimal.' >&2
  exit 2
fi
if ! rustup target list --toolchain 1.93.0 --installed | grep -Fxq wasm32v1-none; then
  echo 'The wasm32v1-none target is required; install it with rustup target add wasm32v1-none --toolchain 1.93.0.' >&2
  exit 2
fi

python3 - "$RPC_PORT" "$P2P_PORT" <<'PY'
import socket
import sys
for raw_port in sys.argv[1:]:
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise SystemExit(f"Invalid local test port: {port}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            raise SystemExit(f"Local port {port} is already in use: {error}")
PY

export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"
export CARGO_INCREMENTAL="${CARGO_INCREMENTAL:-0}"
export CARGO_PROFILE_RELEASE_DEBUG="${CARGO_PROFILE_RELEASE_DEBUG:-0}"
export CARGO_PROFILE_RELEASE_CODEGEN_UNITS="${CARGO_PROFILE_RELEASE_CODEGEN_UNITS:-16}"

python3 "$OVERLAY" apply
PATCH_APPLIED=1
if [[ "${KENAN_RUNTIME_SKIP_BUILD:-0}" != "1" && "${KENAN_RUNTIME_SKIP_BUILD:-0}" != "true" ]]; then
  cargo +1.93.0 build --locked --release --manifest-path "$SDK/templates/solochain/node/Cargo.toml"
fi
NODE_BIN="$SDK/target/release/solochain-template-node"
if [[ ! -x "$NODE_BIN" ]]; then
  echo "Expected Kenan Runtime node binary not found: $NODE_BIN" >&2
  exit 1
fi
mkdir -p "$BASE_PATH"
printf 'Starting Kenan Runtime TEST ONLY on ws://127.0.0.1:%s; P2P stays on 127.0.0.1:%s.\n' "$RPC_PORT" "$P2P_PORT"
printf 'Persistent dev data: %s (not a KEN balance or approved asset). Press Ctrl+C to stop.\n' "$BASE_PATH"
"$NODE_BIN" --dev --chain kenan-dev --alice --force-authoring --base-path "$BASE_PATH" \
  --rpc-port "$RPC_PORT" --rpc-methods safe \
  --listen-addr "/ip4/127.0.0.1/tcp/$P2P_PORT" &
NODE_PID=$!

python3 - "$RPC_PORT" "$NODE_PID" <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request
port, pid = int(sys.argv[1]), int(sys.argv[2])
url = f"http://127.0.0.1:{port}/"
request_id = 0
def rpc(method):
    global request_id
    request_id += 1
    body = json.dumps({"jsonrpc":"2.0","id":request_id,"method":method,"params":[]}).encode()
    request = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request, timeout=2) as response:
        payload = json.load(response)
    if "error" in payload: raise RuntimeError(payload["error"])
    return payload.get("result")
for _ in range(60):
    try:
        print(f"RPC ready; chain={rpc('system_chain')}; finalized={rpc('chain_getFinalizedHead')}", flush=True)
        sys.exit(0)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError):
        try: os.kill(pid, 0)
        except OSError: raise SystemExit("Kenan node exited before RPC became ready")
        time.sleep(1)
raise SystemExit("Timed out waiting for local Kenan Runtime RPC")
PY

wait "$NODE_PID"
NODE_PID=""
