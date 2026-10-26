# API — Python KEN devnet

Default local node: `http://127.0.0.1:8545`. Responses identify `network: devnet-test-only`; token balances are test data only.

## Node

- `GET /health`, `/chain/info`, `/blocks?limit=N` (`1..100`), `/blocks/<height>`, `/accounts/<address>`, `/tx/<64-char-hash>`, `/explorer`.
- `/chain/info` includes `chain_id=kenan-devnet-1`, height, supply, burned amount, account/ledger counts and `fee_base=1000000` (0.001 KEN).
- `POST /tx/submit` accepts a signed transaction `{from,to,amount,fee,nonce,memo,pubkey,signature}`. The node validates address ownership, Ed25519 signature, fixed fee, nonce, balance, and replay status before recording it.
- `POST /faucet {address, amount_base?}` issues devnet-only test tokens. Default amount is 100 KEN; maximum cumulative issuance is 1000 KEN per address. Issuance is committed as a faucet ledger event.
- `POST /mine {}` creates an empty devnet block for tests/explorer.

Faucet and `/mine` are loopback-only by default. When binding to a non-loopback address, set `KENAN_FAUCET_TOKEN` on the node and provide the same value in the `X-Faucet-Token` request header; do so only on a private, access-controlled test network. Do not expose this node publicly. Signed transfers are permissioned by transaction signature/nonce, not by HTTP account authentication. JSON request bodies are limited to 64 KiB and must be objects.

The node’s schema-v2 file stores the ledger plus derived snapshots. At startup, it validates block linkage/hashes and transaction signatures/nonces, replays transfers and faucet issuance, checks supply conservation, then compares the rebuilt balances/nonces/replay set against stored snapshots. It refuses inconsistent or unsupported state instead of silently accepting it. Older files where faucet issuance was not recorded cannot be safely rebuilt; keep a backup and stop for an explicit migration decision if such a file is encountered.

## Backend

Default local backend: `http://127.0.0.1:8000` (`KENAN_NODE_URL` points to the node).

- `GET /health`, `/backend/balance/<address>` (live node proxy), `/exchange/book?pair=KEN/USD-DEMO`, `/exchange/trades`, `/exchange/orders`.
- `POST /exchange/deposit-demo`, `/exchange/orders`, `/exchange/orders/<id>/cancel`.
- `POST /shield/check`, `/intelligence/ask`, `/intelligence/draft`, `/pay/invoice`, `/pay/receipt`, `/circles`, `/circles/<id>/request`, `/academy/account`, `/insights`, `/governance/propose`, `/bridge/transfer` (503, disabled).
- `GET /admin/status` uses `X-Admin-Token: $KENAN_ADMIN_TOKEN`, else returns 503/403.

`POST /pay/receipt` records a caller-provided transaction-hash claim as `pending-verification`; it does not query or verify that transaction against the node. The exchange remains DEMO-only and in-memory.
