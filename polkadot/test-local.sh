#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NODE="${KENAN_NODE_BIN:-$SCRIPT_DIR/sdk/target/release/solochain-template-node}"
RPC_PORT="${KENAN_RPC_PORT:-9944}"
P2P_PORT="${KENAN_P2P_PORT:-30333}"
TEST_DIR="$(mktemp -d -t kenan-polkadot-local.XXXXXX)"
NODE_PID=""
LOG_FILE="$TEST_DIR/node.log"

cleanup() {
  if [[ -n "$NODE_PID" ]] && kill -0 "$NODE_PID" 2>/dev/null; then
    kill "$NODE_PID" 2>/dev/null || true
    wait "$NODE_PID" 2>/dev/null || true
  fi
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

"$NODE" --dev --base-path "$TEST_DIR/chain" --rpc-port "$RPC_PORT" --listen-addr "/ip4/127.0.0.1/tcp/$P2P_PORT" >"$LOG_FILE" 2>&1 &
NODE_PID=$!

python3 - "$RPC_PORT" "$NODE_PID" "$LOG_FILE" <<'PY'
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

ready = False
for _ in range(90):
    try:
        health = rpc("system_health")
        if health is not None:
            ready = True
            break
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError):
        pass
    time.sleep(1)

if not ready:
    try:
        import os
        os.kill(pid, 0)
    except OSError:
        pass
    print("Node RPC did not become ready. Node log:", file=sys.stderr)
    try:
        print(open(log_file, encoding="utf-8", errors="replace").read(), file=sys.stderr)
    except OSError:
        pass
    raise SystemExit(1)

finalized_number = 0
for _ in range(90):
    try:
        finalized_hash = rpc("chain_getFinalizedHead")
        header = rpc("chain_getHeader", [finalized_hash])
        finalized_number = int(header["number"], 16)
        if finalized_number >= 1:
            print(f"RPC ready; finalized block height: {finalized_number}")
            break
    except (OSError, urllib.error.URLError, TimeoutError, RuntimeError, ValueError, KeyError, TypeError):
        pass
    time.sleep(1)
else:
    print("Node RPC is reachable but no finalized block was observed. Node log:", file=sys.stderr)
    try:
        print(open(log_file, encoding="utf-8", errors="replace").read(), file=sys.stderr)
    except OSError:
        pass
    raise SystemExit(1)
PY

KENAN_RPC_WS="ws://127.0.0.1:$RPC_PORT" npm --prefix "$SCRIPT_DIR/integration-test" test
