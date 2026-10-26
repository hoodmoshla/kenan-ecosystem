# Security model — development build

This repository is a prototype, not a reviewed financial product. The Python node has no distributed consensus, and its local JSON ledger is not a production trust anchor.

## Implemented in this snapshot

- `wallet-core/` is the Python reference for BIP39 24-word seed derivation and Ed25519 signing.
- `android-app/` creates/restores the compatible 24-word phrase, stores it as AES-GCM ciphertext in app-private storage, and uses a non-exportable AES key generated in Android Keystore. `allowBackup=false` avoids restoring ciphertext without its device-bound key. The phrase is decrypted briefly in the app process for local Ed25519 signing; the Ed25519 private key itself is derived in software, not held in Android Keystore. There is no independent mobile security audit.
- Android checks the expected `kenan-devnet-1` / `devnet-test-only` network. Before signing, it displays recipient, amount, fee and total, re-reads account nonce/balance, signs locally, submits once, and compares the confirmed RPC record. Ambiguous network results are not auto-resubmitted.
- The Python node validates signature, sender/address relation, amount, fixed fee, nonce, balance and replay. It records each transfer in a hash-linked block. Faucet issuance is recorded as a separate event and capped cumulatively at 1000 KEN/address. Faucet and `/mine` are loopback-only unless an operator token is explicitly configured for a private network.
- On restart, the node rebuilds balances, nonces, faucet issuance, seen transaction IDs, burned fees, supply and transaction lookup from the block ledger. It validates each transfer and event, checks block hashes/supply conservation, and rejects mismatched snapshots. Mutations use a single-process lock and atomic replacement; a failed save rolls back in-memory state.

## Limits and risks

- The local hash chain is not signed by an independent consensus quorum. A person able to rewrite the entire local ledger and recompute its hashes can still replace it. Run a single node process against one state file; multi-process writers, remote administration and production backups/restore drills are not implemented.
- Older JSON snapshots may contain faucet-created balances that were never written to blocks. The new loader refuses to trust a snapshot that cannot be rebuilt. Preserve the original file and require an explicit migration decision; no such `data/chain.json` was present in the checked workspace.
- The devnet faucet is not an issuance policy for a public network. The loopback default and per-address cap reduce accidental exposure, but do not turn this prototype into a secure public faucet.
- HTTP is enabled only in the Debug app manifest for local testing; the base/Release manifest keeps cleartext disabled. No production TLS endpoint is configured.
- Debug APK uses the standard Android Debug signing certificate. No project release key, signing-key fingerprint verification, independent security audit, penetration test, production custody, real funds, mainnet or audited bridge exists.
- Demo exchange balances are in memory and do not settle to the chain. Backend payment receipt claims remain unverified against the node. Governance is off-chain signalling and bridge transfers are disabled.

Do not expose the node or faucet to untrusted networks, store real assets here, or describe devnet balances as monetary value. See `docs/STATUS.md`, `docs/OPERATIONS.md` and `docs/POLKADOT.md`.
