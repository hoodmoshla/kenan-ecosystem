const assert = require('node:assert/strict');
const { ApiPromise, Keyring, WsProvider } = require('@polkadot/api');
const { cryptoWaitReady } = require('@polkadot/util-crypto');

const endpoint = process.env.KENAN_RUNTIME_RPC_WS || 'ws://127.0.0.1:9950';
const endpointUrl = new URL(endpoint);
assert(
  ['127.0.0.1', 'localhost', '[::1]'].includes(endpointUrl.hostname),
  'Kenan Runtime integration tests are restricted to a loopback node.'
);

function asBigInt(value) {
  return BigInt(value.toString());
}

async function submitAndWaitFinalized(extrinsic) {
  return new Promise((resolve, reject) => {
    let unsubscribe;
    let settled = false;
    const timeout = setTimeout(() => {
      if (!settled) {
        settled = true;
        if (unsubscribe) unsubscribe();
        reject(new Error('Timed out waiting for the Kenan Runtime test extrinsic to finalize.'));
      }
    }, 120_000);

    extrinsic.send((result) => {
      if (result.status.isInvalid || result.status.isDropped || result.status.isUsurped) {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        reject(new Error(`Extrinsic ${result.status.type}: ${result.status.toString()}`));
        return;
      }
      if (result.status.isFinalized && !settled) {
        settled = true;
        clearTimeout(timeout);
        resolve({
          blockHash: result.status.asFinalized.toHex(),
          txHash: result.txHash.toHex(),
        });
        if (unsubscribe) unsubscribe();
      }
    }).then((stop) => {
      unsubscribe = stop;
      if (settled && unsubscribe) unsubscribe();
    }).catch((error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(error);
    });
  });
}

async function eventsForExtrinsic(api, blockHash, extrinsicHash) {
  const block = await api.rpc.chain.getBlock(blockHash);
  const index = block.block.extrinsics.findIndex((item) => item.hash.toHex() === extrinsicHash);
  assert(index >= 0, `Finalized block ${blockHash} did not contain ${extrinsicHash}.`);
  const records = await api.query.system.events.at(blockHash);
  return records.filter(({ phase }) =>
    phase.isApplyExtrinsic && phase.asApplyExtrinsic.toNumber() === index
  );
}

async function finalizedState(api, blockHash, addresses) {
  const at = await api.at(blockHash);
  const result = new Map();
  for (const address of addresses) {
    const account = await at.query.system.account(address);
    result.set(address, {
      free: asBigInt(account.data.free),
      nonce: asBigInt(account.nonce),
    });
  }
  return result;
}

async function main() {
  await cryptoWaitReady();
  const api = await ApiPromise.create({ provider: new WsProvider(endpoint), noInitWarn: true });
  try {
    const chainName = (await api.rpc.system.chain()).toString();
    assert.equal(chainName, 'Kenan Development (TEST ONLY)');

    const runtimeVersion = await api.rpc.state.getRuntimeVersion();
    assert.equal(runtimeVersion.specName.toString(), 'kenan-test-runtime');
    assert.equal(runtimeVersion.implName.toString(), 'kenan-test-runtime');

    const properties = (await api.rpc.system.properties()).toJSON();
    const symbol = Array.isArray(properties.tokenSymbol)
      ? properties.tokenSymbol[0]
      : properties.tokenSymbol;
    assert.equal(symbol, 'UNIT', 'The test chain must expose the upstream template symbol explicitly.');
    assert.equal(Number(properties.tokenDecimals), 12, 'The test chain must expose the upstream template precision explicitly.');
    assert.ok(api.tx.balances?.transferKeepAlive, 'Balances transfer call is missing from runtime metadata.');
    assert.ok(api.tx.sudo?.sudo, 'Sudo call is missing from runtime metadata.');
    assert.ok(api.rpc.payment?.queryInfo, 'Transaction-payment RPC is not registered.');
    console.log('PASS: test-only chain identity, runtime metadata and inherited display properties');

    const keyring = new Keyring({ type: 'sr25519' });
    const alice = keyring.addFromUri('//Alice');
    const bob = keyring.addFromUri('//Bob');
    const aliceStash = keyring.addFromUri('//Alice//stash');
    const bobStash = keyring.addFromUri('//Bob//stash');
    const endowed = [alice, bob, aliceStash, bobStash];

    const genesisHash = await api.rpc.chain.getBlockHash(0);
    const atGenesis = await api.at(genesisHash);
    const genesisIssuance = asBigInt(await atGenesis.query.balances.totalIssuance());
    let endowedTotal = 0n;
    for (const account of endowed) {
      const info = await atGenesis.query.system.account(account.address);
      endowedTotal += asBigInt(info.data.free);
    }
    assert.ok(genesisIssuance > 0n, 'Development genesis issued no balances.');
    assert.equal(genesisIssuance, endowedTotal, 'Genesis issuance does not reconcile to the upstream development endowments.');
    console.log(`PASS: genesis issuance reconciles exactly across ${endowed.length} upstream development-only accounts`);

    const headBefore = await api.rpc.chain.getFinalizedHead();
    const before = await finalizedState(api, headBefore, [alice.address, bob.address]);
    const amount = 1_000_000n;
    const nonce = asBigInt(await api.rpc.system.accountNextIndex(alice.address));
    const transfer = api.tx.balances.transferKeepAlive(bob.address, amount.toString());
    await transfer.signAsync(alice, { nonce: nonce.toString(), era: 0 });
    const feeQuote = await api.rpc.payment.queryInfo(transfer.toHex());
    assert.ok(asBigInt(feeQuote.partialFee) > 0n, 'Transaction payment returned a zero fee quote.');

    const included = await submitAndWaitFinalized(transfer);
    const transferEvents = await eventsForExtrinsic(api, included.blockHash, included.txHash);
    assert.ok(transferEvents.some(({ event }) => event.section === 'system' && event.method === 'ExtrinsicSuccess'));
    assert.ok(transferEvents.some(({ event }) => event.section === 'balances' && event.method === 'Transfer'));
    const after = await finalizedState(api, included.blockHash, [alice.address, bob.address]);
    const senderDelta = before.get(alice.address).free - after.get(alice.address).free;
    const recipientDelta = after.get(bob.address).free - before.get(bob.address).free;
    assert.equal(recipientDelta, amount, 'Recipient did not receive the exact finalized transfer amount.');
    assert.ok(senderDelta > amount, 'Sender balance did not include a positive transaction fee.');
    const actualFee = senderDelta - amount;
    assert.ok(actualFee > 0n);
    assert.equal(after.get(alice.address).nonce, before.get(alice.address).nonce + 1n);
    console.log(`PASS: finalized balance transfer, nonce and positive runtime fee (${actualFee} base units)`);

    const bobNonce = asBigInt(await api.rpc.system.accountNextIndex(bob.address));
    const unauthorizedCall = api.tx.sudo.sudo(api.tx.system.remark('test-only unauthorized Sudo probe'));
    await unauthorizedCall.signAsync(bob, { nonce: bobNonce.toString(), era: 0 });
    const rejected = await submitAndWaitFinalized(unauthorizedCall);
    const rejectedEvents = await eventsForExtrinsic(api, rejected.blockHash, rejected.txHash);
    const failure = rejectedEvents.find(({ event }) => event.section === 'system' && event.method === 'ExtrinsicFailed');
    assert.ok(failure, 'A non-Sudo development account unexpectedly executed Sudo.');
    const decodedFailure = api.registry.findMetaError(failure.event.data[0].asModule);
    assert.equal(decodedFailure.section.toLowerCase(), 'sudo');
    assert.equal(decodedFailure.name, 'RequireSudo');

    const aliceNonce = asBigInt(await api.rpc.system.accountNextIndex(alice.address));
    const authorizedCall = api.tx.sudo.sudo(api.tx.system.remark('test-only authorized Sudo probe'));
    await authorizedCall.signAsync(alice, { nonce: aliceNonce.toString(), era: 0 });
    const authorized = await submitAndWaitFinalized(authorizedCall);
    const authorizedEvents = await eventsForExtrinsic(api, authorized.blockHash, authorized.txHash);
    assert.ok(authorizedEvents.some(({ event }) => event.section === 'system' && event.method === 'ExtrinsicSuccess'));
    assert.ok(authorizedEvents.some(({ event }) => event.section === 'sudo' && event.method === 'Sudid'));
    console.log('PASS: development Sudo rejects Bob and permits Alice for inert System.remark only');
  } finally {
    await api.disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
