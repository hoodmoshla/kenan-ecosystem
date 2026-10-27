#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NODE="$SCRIPT_DIR/sdk/target/release/solochain-template-node"
RPC_PORT="${KENAN_RPC_PORT:-9944}"
P2P_PORT="${KENAN_P2P_PORT:-30333}"

if [[ ! -x "$NODE" ]]; then
  echo "Node binary is missing. Build it first with ./polkadot/build-node.sh" >&2
  exit 2
fi

# Keep RPC and P2P explicitly confined to loopback; unsafe local RPC methods are never exposed publicly.
exec "$NODE" --dev --rpc-addr 127.0.0.1 --rpc-port "$RPC_PORT" --rpc-methods unsafe \
  --listen-addr "/ip4/127.0.0.1/tcp/$P2P_PORT"
