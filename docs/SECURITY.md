# Threat model & key management (summary)

- Keys: user-held only. Wallet signs locally (Python reference + Rust
  core + Android Keystore design). No server, log, crash report or
  AI-assistant request ever receives a seed phrase or private key.
  The Intelligence module refuses payloads containing key fields.
- Storage: Android release build must use AndroidKeyStore AES/GCM for
  the seed, never plaintext SharedPreferences/SQLite/files. Biometrics
  unlock the local key; they are NOT a backup — backup completeness is
  proven by re-deriving the address from the phrase (tested).
- Transactions: Shield shows blocked/warning/ok/not_checked — never
  "safe" without data. Simulation is not a guarantee. High fees and
  untrusted recipients warn before signing; signing needs explicit user
  confirmation showing recipient, network, amount and fee.
- Backend: admin endpoints disabled unless KENAN_ADMIN_TOKEN is set and
  matched; demo exchange balances are isolated from chain balances;
  audit events append to data/audit.log.
- Supply: the admin UI cannot mint chain balances or edit chain state.
  Only chain rules (genesis/faucet/fees on devnet) change supply, and
  the faucet exists ONLY on devnet.
- What is NOT yet audited: everything. No independent audit has been
  performed; do not launch publicly on the basis of this snapshot.
