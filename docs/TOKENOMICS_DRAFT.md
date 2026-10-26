# KEN Tokenomics — DRAFT for review before any public network

This is a draft, not a published monetary policy. Final numbers require
review and must be re-issued before mainnet; devnet numbers below are
test-only and do not bind mainnet.

- Unit: 1 KEN = 10^9 base units. Devnet chain id: kenan-devnet-1.
- Devnet genesis: 100,000,000 test-KEN credited to the fee-collector
  pseudo-account (see blockchain/kenan_chain.py). This is test supply.
- Fee (devnet, verified in code): flat 0.001 KEN per transfer;
  50% burned (reduces total supply, recorded per block), 50% to the
  fee-collector. No percentage fee, no hidden fee.
- Proposed mainnet alternatives to decide (do not present as decided):
  A) Fixed cap 1,000,000,000 KEN, validator rewards from fees only.
  B) Fixed cap + scheduled emission with a halving-style decay.
  C) No hard cap, emission governed by on-chain vote with a published
     inflation ceiling. Each option's inflation / concentration risk
     must be modelled before choosing.
- Circulating vs total vs locked vs burned: circulating = total supply
  minus locked allocations minus burned, all read from chain state —
  never from an app database. Vesting schedules, team/treasury/community
  allocations are UNDECIDED and must be published with release dates
  before any sale or distribution.
- Risks: concentration at genesis, fee-market volatility, validator
  centralisation on a small devnet, bridge/custody risk (bridge disabled).
