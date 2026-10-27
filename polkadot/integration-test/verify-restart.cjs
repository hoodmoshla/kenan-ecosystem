const assert = require('node:assert/strict');
const fs = require('node:fs');
const { ApiPromise, WsProvider } = require('@polkadot/api');

const endpoint = process.env.KENAN_RPC_WS || 'ws://127.0.0.1:9944';
const endpointUrl = new URL(endpoint);
assert(['127.0.0.1', 'localhost', '[::1]'].includes(endpointUrl.hostname), 'Restart verification is local-node only.');
const snapshotPath = process.env.KENAN_STATE_SNAPSHOT;
assert(snapshotPath && fs.existsSync(snapshotPath), 'KENAN_STATE_SNAPSHOT must point to the pre-restart checkpoint.');
const expected = JSON.parse(fs.readFileSync(snapshotPath, 'utf8'));

async function main() {
  const provider = new WsProvider(endpoint);
  const api = await ApiPromise.create({ provider, noInitWarn: true });
  try {
    assert.equal((await api.rpc.system.chain()).toString(), expected.chain, 'Chain identity changed after restart.');
    assert.equal((await api.rpc.chain.getBlockHash(0)).toHex(), expected.genesisHash, 'Genesis hash changed after restart.');

    const finalizedHash = await api.rpc.chain.getFinalizedHead();
    const finalizedHeader = await api.rpc.chain.getHeader(finalizedHash);
    const finalizedHeight = BigInt(finalizedHeader.number.toString());
    assert(finalizedHeight >= BigInt(expected.finalizedHeight), 'Finalized chain head regressed after restart.');

    for (const [name, address] of Object.entries(expected.accounts)) {
      const account = await api.query.system.account(address);
      assert.equal(account.data.free.toString(), expected.final[name === 'alice' ? 'aliceFree' : `${name}Free`], `${name} free balance was not restored.`);
      if (name === 'alice') {
        assert.equal(account.nonce.toString(), expected.final.aliceNonce, 'Alice account nonce was not restored.');
        assert.equal(
          (await api.rpc.system.accountNextIndex(address)).toString(),
          expected.final.aliceNonce,
          'Alice next nonce differs from the persisted account nonce.'
        );
      }
    }

    for (const transfer of expected.transfers) {
      const block = await api.rpc.chain.getBlock(transfer.blockHash);
      assert(
        block.block.extrinsics.some((extrinsic) => extrinsic.hash.toHex() === transfer.txHash),
        `Transaction ${transfer.txHash} is missing from its persisted finalized block.`
      );
    }

    console.log(JSON.stringify({
      result: 'passed',
      check: 'node stop/restart with existing chain database; RPC recovery; state and finalized transaction persistence',
      chain: expected.chain,
      genesisHash: expected.genesisHash,
      finalizedHeightBeforeRestart: expected.finalizedHeight,
      finalizedHeightAfterRestart: finalizedHeight.toString(),
      balancesAndNonceRestored: true,
      finalizedTransactionsRestored: expected.transfers.length,
    }, null, 2));
  } finally {
    await api.disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
