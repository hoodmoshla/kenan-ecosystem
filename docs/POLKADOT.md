# Kenan on Polkadot SDK — transition plan (devnet stays live until this passes)

Status: PLAN + TARGET VERSION ONLY. No Substrate node is built yet, and
nothing here replaces the working Python devnet (`blockchain/`).
Do not present this document as a running Substrate chain.

## Target (to re-verify against paritytech/polkadot-sdk releases at build time)
- Latest stable found in search index at planning time: `polkadot-stable2606-2`,
  node version v1.24.2. Re-check the official releases page before pinning;
  do NOT pin from memory or from a mirror alone.
- Starting point: the official Substrate node template for that stable
  branch (solo chain, not a parachain in phase 1).
- Consensus: Aura (block production) + GRANDPA (finality), the template
  defaults. Single-validator devnet first, then a 2–3 validator testnet
  with keys inserted via `node key insert` (Aura Sr25519, Grandpa Ed25519).
- Runtime pallets (minimum): System, Timestamp, Aura, Grandpa, Balances,
  TransactionPayment, Sudo (devnet only — must be removed before any
  public testnet), plus a Kenan metadata pallet or Balances configuration
  carrying: token symbol KEN, decimals 9 (to match the devnet: 1 KEN =
  10**9 base units), existential deposit to be decided in the tokenomics
  review, and the flat-fee rule re-implemented as a fee model, not as
  hard-coded Python constants.

## Why this VM cannot build it now
- Full Substrate release builds need several GB of RAM and a long build
  (wasm runtime + native node). This VM has 2 CPUs and very little free
  RAM at planning time. Attempting it here would OOM and produce no
  verifiable artefact, so it is deliberately NOT started here.
- Build it on a larger machine or a GitHub Actions runner with at least
  16 GB RAM, Rust stable + `wasm32-unknown-unknown` target, and the
  template's documented prerequisites. The CI job for it must run
  `cargo check` first (fast failure) before any release build.

## RPC / wallet compatibility map (the contract the Substrate node must meet)
The Python devnet exposes a small HTTP JSON API; the Android app and
backend currently use it. The Substrate node speaks JSON-RPC over
WebSocket/HTTP with different method names, so the wallet needs an
adapter, not a rewrite of its UX:
- `GET /chain/info` (height, supply, burned) -> `chain_getHeader` +
  `system_properties` + Balances total issuance query.
- `GET /accounts/<addr>` (balance, nonce) -> `system_account` storage
  query for that SS58 address (note: SS58 address format differs from
  the devnet `kenan1…` format — the wallet must support both during
  migration, and old devnet addresses do NOT carry over automatically).
- `POST /tx/submit` (Ed25519-signed, chain-id in message, sequential
  nonce, fee) -> Substrate extrinsic submission: the signing payload
  is the SCALE-encoded extrinsic, the nonce is the account nonce from
  `system_account`, and the fee is estimated via `payment_queryInfo`.
  The Python canonical message format must NOT be sent to Substrate;
  it is devnet-only.
- Explorer: Substrate blocks are fetched via `chain_getBlock`, not
  `/blocks/<height>`.

## Integration-test gate before switching the wallet's default network
1. Substrate dev node produces blocks locally and finalises them.
2. A test account receives KEN (Balances), sends a transfer, and the
   balance + nonce read back through the adapter match the chain.
3. A replayed extrinsic and a wrong-nonce extrinsic are both rejected.
4. The Python devnet still runs unchanged, and the wallet can switch
   networks explicitly (never silently) — only then may the Substrate
  chain become the default in a release build.
5. No mainnet, no real funds, no public listing at any point in this
   transition; the tokenomics document remains a draft until reviewed.

## Address-format warning (do not hide this)
Devnet addresses (`kenan1` + SHA-256(pubkey)[:20]) are NOT Substrate
SS58 addresses. A migration must re-derive or re-issue addresses and
tell users explicitly that devnet balances are test tokens and do not
migrate as value. Any document claiming otherwise is wrong.
