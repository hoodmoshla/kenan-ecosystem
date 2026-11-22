#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NODE="${KENAN_NODE_BIN:-$SCRIPT_DIR/sdk/target/release/solochain-template-node}"
RPC_PORT="${KENAN_RPC_PORT:-9944}"
P2P_PORT="${KENAN_P2P_PORT:-30333}"
TEST_DIR="$(mktemp -d -t kenan-polkadot-local.XXXXXX)"
CHAIN_DIR="$TEST_DIR/chain"
SNAPSHOT="$TEST_DIR/restart-checkpoint.json"
NODE_PID=""
START_COUNT=0

node_is_running() {
  [[ -n "$NODE_PID" ]] || return 1
  local state
  state="$(ps -o stat= -p "$NODE_PID" 2>/dev/null | tr -d ' ' || true)"
  [[ -n "$state" && "$state" != Z* && "$state" != z* ]]
}

stop_node() {
  if node_is_running; then
    kill -TERM "$NODE_PID" 2>/dev/null || true
    for _ in $(seq 1 30); do
      if ! node_is_running; then break; fi
      sleep 1
    done
    if node_is_running; then
      echo "Node did not stop gracefully; terminating its local test process." >&2
      kill -KILL "$NODE_PID" 2>/dev/null || true
    fi
    wait "$NODE_PID" 2>/dev/null || true
  fi
  NODE_PID=""
}

cleanup() {
  stop_node
  rm -rf -- "$TEST_DIR"
}
trap cleanup EXIT INT TERM

if [[ ! -x "$NODE" ]]; then
  echo "Node binary is missing: $NODE. Run ./polkadot/build-node.sh first." >&2
  exit 2
fi
if [[ ! -d "$SCRIPT_DIR/integration-test/node_modules/@polkadot/api" ]]; then
  echo "Test dependencies are missing. Run: npm ci --prefix polkadot/integration-test" >&2
  exit 2
fi

start_node() {
  START_COUNT=$((START_COUNT + 1))
  local log_file="$TEST_DIR/node-$START_COUNT.log"
  python3 - "$RPC_PORT" "$P2P_PORT" <<'PY'
import socket
import sys

for raw_port in sys.argv[1:]:
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise SystemExit(f"Invalid local test port: {port}")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Permit rebinding after the just-stopped node's closed TCP connections enter TIME_WAIT.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            raise SystemExit(f"Local test port {port} is already in use: {error}")
PY
  "$NODE" --dev --base-path "$CHAIN_DIR" --rpc-port "$RPC_PORT" --listen-addr "/ip4/127.0.0.1/tcp/$P2P_PORT" >"$log_file" 2>&1 &
  NODE_PID=$!
  echo "Started local test node (run $START_COUNT, PID $NODE_PID)."
  python3 - "$RPC_PORT" "$NODE_PID" "$log_file" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

port, pid, log_file = sys.argv[1], int(sys.argv[2]), sys.argv[3]
url = f"http://127.0.0.1:{port}"
request_id = 0

def rpc(method, params=None):
    global request_id
    request_id += 1
    body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or []}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as response:
        payload = json.load(response)
    if "error" in payload:
        raise RuntimeError(f"{method}: {payload['error']}")
    return payload.get("result")

for _ in range(120):
    try:
        health = rpc("system_health")
        if health is not None:
            finalized_hash = rpc("chain_getFinalizedHead")
            header = rpc("chain_getHeader", [finalized_hash])
            height = int(header["number"], 16)
            print(f"PASS RPC ready; finalized block height: {height}")
            sys.exit(0)
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError, KeyError, TypeError):
        pass
    try:
        import os
        os.kill(pid, 0)
    except OSError:
        print("Node process exited before RPC became ready. Node log:", file=sys.stderr)
        try:
            print(open(log_file, encoding="utf-8", errors="replace").read(), file=sys.stderr)
        except OSError:
            pass
        sys.exit(1)
    time.sleep(1)
print("Node RPC did not become ready within 120 seconds. Node log:", file=sys.stderr)
try:
    print(open(log_file, encoding="utf-8", errors="replace").read(), file=sys.stderr)
except OSError:
    pass
sys.exit(1)
PY
}

assert_rpc_unavailable() {
  python3 - "$RPC_PORT" <<'PY'
import json
import sys
import urllib.error
import urllib.request

url = f"http://127.0.0.1:{sys.argv[1]}"
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "system_health", "params": []}).encode()
req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=2) as response:
        response.read()
except (OSError, urllib.error.URLError, TimeoutError):
    print("PASS RPC outage observed while the node was stopped")
else:
    print("RPC unexpectedly remained reachable after stopping the node", file=sys.stderr)
    sys.exit(1)
PY
}

start_node
KENAN_RPC_WS="ws://127.0.0.1:$RPC_PORT" \
KENAN_STATE_SNAPSHOT="$SNAPSHOT" \
  npm --prefix "$SCRIPT_DIR/integration-test" test

stop_node
assert_rpc_unavailable
start_node
KENAN_RPC_WS="ws://127.0.0.1:$RPC_PORT" \
KENAN_STATE_SNAPSHOT="$SNAPSHOT" \
  node "$SCRIPT_DIR/integration-test/verify-restart.cjs"

printf '%s\n' 'PASS local node restart, RPC outage/recovery, persisted balances, nonce, and finalized extrinsics'
