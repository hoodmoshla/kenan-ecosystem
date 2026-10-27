const assert = require('node:assert/strict');
const { ApiPromise, Keyring, WsProvider } = require('@polkadot/api');
const { cryptoWaitReady } = require('@polkadot/util-crypto');

const endpoint = process.env.KENAN_RPC_WS || 'ws://127.0.0.1:9944';
const endpointUrl = new URL(endpoint);
assert(
  ['127.0.0.1', 'localhost', '[::1]'].includes(endpointUrl.hostname),
  'This integration test is restricted to a local development node.'
);
const amount = BigInt(process.env.KENAN_TEST_TRANSFER_BASE || '1000000000');
assert(amount > 0n, 'KENAN_TEST_TRANSFER_BASE must be a positive integer.');

function dispatchErrorText(api, error) {
  if (error.isModule) {
    const meta = api.registry.findMetaError(error.asModule);
    return `${meta.section}.${meta.name}: ${meta.docs.join(' ')}`;
  }
  return error.toString();
}

async function main() {
  await cryptoWaitReady();
  const provider = new WsProvider(endpoint);
  const api = await ApiPromise.create({ provider, noInitWarn: true });
  let unsubscribe;

  try {
    const chain = (await api.rpc.system.chain()).toString();
    const genesisHash = (await api.rpc.chain.getBlockHash(0)).toHex();
    const keyring = new Keyring({ type: 'sr25519' });
    const alice = keyring.addFromUri('//Alice');
    const bob = keyring.addFromUri('//Bob');
    const nonceBefore = (await api.rpc.system.accountNextIndex(alice.address)).toBigInt();
    const aliceBefore = BigInt((await api.query.system.account(alice.address)).data.free.toString());
    const bobBefore = BigInt((await api.query.system.account(bob.address)).data.free.toString());

    const transfer = api.tx.balances.transferKeepAlive(bob.address, amount.toString());
    const finalized = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Timed out waiting for transaction finality.')), 120_000);
      transfer
        .signAndSend(alice, { nonce: Number(nonceBefore) }, (result) => {
          if (result.dispatchError) {
            clearTimeout(timeout);
            reject(new Error(`Transfer dispatch failed: ${dispatchErrorText(api, result.dispatchError)}`));
            return;
          }
          if (result.status.isFinalized) {
            clearTimeout(timeout);
            resolve(result);
          }
        })
        .then((stop) => {
          unsubscribe = stop;
        })
        .catch((error) => {
          clearTimeout(timeout);
          reject(error);
        });
    });

    const successEvent = finalized.events.some(
      ({ event }) => event.section === 'system' && event.method === 'ExtrinsicSuccess'
    );
    const transferEvent = finalized.events.some(
      ({ event }) => event.section === 'balances' && event.method === 'Transfer'
    );
    assert(successEvent, 'Finalized extrinsic did not emit System.ExtrinsicSuccess.');
    assert(transferEvent, 'Finalized extrinsic did not emit Balances.Transfer.');

    const finalizedApi = await api.at(finalized.status.asFinalized);
    const aliceAfter = BigInt((await finalizedApi.query.system.account(alice.address)).data.free.toString());
    const bobAfter = BigInt((await finalizedApi.query.system.account(bob.address)).data.free.toString());
    const nonceAfter = (await api.rpc.system.accountNextIndex(alice.address)).toBigInt();

    assert.equal(bobAfter - bobBefore, amount, 'Recipient balance did not increase by the signed amount.');
    const transactionFee = aliceBefore - aliceAfter - amount;
    assert(transactionFee > 0n, 'Sender balance did not include a positive transaction fee.');
    assert.equal(nonceAfter, nonceBefore + 1n, 'Sender nonce was not incremented exactly once.');

    console.log(JSON.stringify({
      result: 'passed',
      chain,
      genesisHash,
      txHash: finalized.txHash.toHex(),
      finalizedBlockHash: finalized.status.asFinalized.toHex(),
      signerNonceBefore: nonceBefore.toString(),
      signerNonceAfter: nonceAfter.toString(),
      transferredBaseUnits: amount.toString(),
      transactionFeeBaseUnits: transactionFee.toString(),
      recipientBalanceBefore: bobBefore.toString(),
      recipientBalanceAfter: bobAfter.toString(),
      senderBalanceBefore: aliceBefore.toString(),
      senderBalanceAfter: aliceAfter.toString(),
      signingKeys: 'local development-only Alice/Bob',
    }, null, 2));
  } finally {
    if (unsubscribe) unsubscribe();
    await api.disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
