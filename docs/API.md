# API (devnet)

## Node — http://127.0.0.1:8545
- GET /health, /chain/info, /blocks?limit=N, /blocks/<height>,
  /accounts/<address>, /tx/<hash>, /explorer (HTML, RTL Arabic)
- POST /tx/submit — signed tx {from,to,amount,fee,nonce,memo,pubkey,signature}
- POST /faucet {address} — devnet test tokens only, max 1000 KEN
- POST /mine — empty block (explorer/tests)

## Backend — http://127.0.0.1:8000 (KENAN_NODE_URL points at the node)
- GET /health, /backend/balance/<address> (live node proxy),
  /exchange/book?pair=KEN/USD-DEMO, /exchange/trades, /exchange/orders
- POST /exchange/deposit-demo, /exchange/orders, /exchange/orders/<id>/cancel
- POST /shield/check, /intelligence/ask, /intelligence/draft,
  /pay/invoice, /pay/receipt, /circles, /circles/<id>/request,
  /academy/account, /insights, /governance/propose, /bridge/transfer (503, disabled)
- GET /admin/status — header X-Admin-Token = $KENAN_ADMIN_TOKEN, else 503/403
