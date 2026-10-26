# Operations & what remains before any public launch

Local run (devnet, test tokens only):
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 -m blockchain.node_server --port 8545 --data data/chain.json
KENAN_NODE_URL=http://127.0.0.1:8545 python3 -m backend.app --port 8000
# Explorer: http://127.0.0.1:8545/explorer
python3 -m unittest discover -s tests -v
```

Production gaps (all blocking for public launch):
- Substrate/Polkadot SDK node + testnet validators + runtime upgrade plan.
- PostgreSQL (replacing in-memory/SQLite demo stores), Redis only if a
  measured need appears, secrets manager, backups + tested restore.
- Price feed with source + timestamp, or keep PnL disabled.
- LLM provider (optional, privacy-reviewed) or keep rule-based assistant.
- Independent security audit, penetration test, legal/licensing review.
- Signing-key custody for release APKs, CI secrets, incident runbook.
- No real funds, no public listing, no bridge until every item above
  that applies is closed and signed off.
