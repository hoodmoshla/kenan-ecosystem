const assert = require('node:assert/strict');
const fs = require('node:fs');
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

function pascalCase(value) {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

function rawAccountId(api, value) {
  return api.createType('AccountId32', value.toString()).toHex().toLowerCase();
}

async function finalizedObserverBundle(api, throughHeight, targetTxHash, identity) {
  const genesisHash = (await api.rpc.chain.getBlockHash(0)).toHex().toLowerCase();
  const assetId = `substrate:balances:native:${identity.tokenSymbol}:${identity.tokenDecimals}`;
  const blocks = [];
  for (let height = 0; height <= throughHeight; height += 1) {
    const blockHash = (await api.rpc.chain.getBlockHash(height)).toHex();
    const signedBlock = await api.rpc.chain.getBlock(blockHash);
    const header = signedBlock.block.header;
    assert.equal(header.number.toNumber(), height, `Unexpected canonical height at ${blockHash}`);
    const records = await api.query.system.events.at(blockHash);
    const transfers = [];
    records.forEach(({ phase, event }, eventIndex) => {
      if (event.section !== 'balances' || event.method !== 'Transfer' || !phase.isApplyExtrinsic) return;
      const extrinsicIndex = phase.asApplyExtrinsic.toNumber();
      const extrinsic = signedBlock.block.extrinsics[extrinsicIndex];
      assert.ok(extrinsic, `Transfer event references missing extrinsic ${extrinsicIndex}`);
      const success = records.some(({ phase: candidatePhase, event: candidateEvent }) =>
        candidatePhase.isApplyExtrinsic &&
        candidatePhase.asApplyExtrinsic.toNumber() === extrinsicIndex &&
        candidateEvent.section === 'system' && candidateEvent.method === 'ExtrinsicSuccess'
      );
      const method = extrinsic.method;
      const args = method.args;
      const eventData = event.data;
      transfers.push({
        tx_hash: extrinsic.hash.toHex().toLowerCase(),
        extrinsic_hex: extrinsic.toHex(),
        extrinsic_index: extrinsicIndex,
        phase_extrinsic_index: phase.asApplyExtrinsic.toNumber(),
        event_index: eventIndex,
        pallet: pascalCase(event.section),
        event_name: event.method,
        asset_id: assetId,
        system_success_phase_extrinsic_index: success ? extrinsicIndex : null,
        sender: eventData[0].toHex().toLowerCase(),
        beneficiary: eventData[1].toHex().toLowerCase(),
        amount_base_units: eventData[2].toString(),
        call_pallet: pascalCase(method.section),
        call_name: method.method,
        call_sender: extrinsic.isSigned ? rawAccountId(api, extrinsic.signer) : '',
        call_beneficiary: args.length > 0 ? rawAccountId(api, args[0]) : '',
        call_amount_base_units: args.length > 1 ? args[1].toString() : '0',
        extrinsic_success: success,
      });
    });
    blocks.push({
      genesis_hash: genesisHash,
      height,
      block_hash: blockHash.toLowerCase(),
      parent_hash: header.parentHash.toHex().toLowerCase(),
      finalized: true,
      chain_name: identity.chainName,
      spec_name: identity.specName,
      spec_version: identity.specVersion,
      token_symbol: identity.tokenSymbol,
      token_decimals: identity.tokenDecimals,
      transfers,
    });
  }
  const target = blocks.flatMap((block) => block.transfers).find((transfer) => transfer.tx_hash === targetTxHash.toLowerCase());
  assert.ok(target, `Finalized history does not contain transfer ${targetTxHash}`);
  return {
    genesis_hash: genesisHash,
    chain_name: identity.chainName,
    spec_name: identity.specName,
    spec_version: identity.specVersion,
    token_symbol: identity.tokenSymbol,
    token_decimals: identity.tokenDecimals,
    asset_id: assetId,
    target_tx_hash: target.tx_hash,
    target_beneficiary: target.beneficiary,
    target_amount_base_units: target.amount_base_units,
    blocks,
  };
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

    const properties = await api.rpc.system.properties();
    assert.ok(properties.tokenSymbol.isSome, 'Chain token symbol was not exposed.');
    assert.ok(properties.tokenDecimals.isSome, 'Chain token precision was not exposed.');
    const symbol = properties.tokenSymbol.unwrap()[0]?.toString();
    const decimals = properties.tokenDecimals.unwrap()[0]?.toNumber();
    assert.equal(symbol, 'UNIT', 'The test chain must expose the upstream template symbol explicitly.');
    assert.equal(decimals, 12, 'The test chain must expose the upstream template precision explicitly.');
    assert.ok(api.tx.balances?.transferKeepAlive, 'Balances transfer call is missing from runtime metadata.');
    assert.ok(api.tx.sudo?.sudo, 'Sudo call is missing from runtime metadata.');
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

    const included = await submitAndWaitFinalized(transfer);
    const transferEvents = await eventsForExtrinsic(api, included.blockHash, included.txHash);
    assert.ok(transferEvents.some(({ event }) => event.section === 'system' && event.method === 'ExtrinsicSuccess'));
    assert.ok(transferEvents.some(({ event }) => event.section === 'balances' && event.method === 'Transfer'));
    const feeEvent = transferEvents.find(({ event }) => event.section === 'transactionPayment' && event.method === 'TransactionFeePaid');
    assert.ok(feeEvent, 'The finalized transfer did not emit TransactionFeePaid.');
    const after = await finalizedState(api, included.blockHash, [alice.address, bob.address]);
    const senderDelta = before.get(alice.address).free - after.get(alice.address).free;
    const recipientDelta = after.get(bob.address).free - before.get(bob.address).free;
    assert.equal(recipientDelta, amount, 'Recipient did not receive the exact finalized transfer amount.');
    assert.ok(senderDelta > amount, 'Sender balance did not include a positive transaction fee.');
    const actualFee = senderDelta - amount;
    assert.ok(actualFee > 0n);
    assert.equal(actualFee, asBigInt(feeEvent.event.data[1]), 'Balance delta does not match the finalized fee event.');
    assert.equal(after.get(alice.address).nonce, before.get(alice.address).nonce + 1n);
    console.log(`PASS: finalized balance transfer, nonce and positive runtime fee (${actualFee} base units)`);

    const finalizedHeader = await api.rpc.chain.getHeader(included.blockHash);
    const observerBundle = await finalizedObserverBundle(
      api,
      finalizedHeader.number.toNumber(),
      included.txHash,
      {
        chainName,
        specName: runtimeVersion.specName.toString(),
        specVersion: runtimeVersion.specVersion.toNumber(),
        tokenSymbol: symbol,
        tokenDecimals: decimals,
      },
    );
    const observerBundlePath = process.env.KENAN_RUNTIME_OBSERVER_BUNDLE_PATH;
    assert.ok(observerBundlePath, 'Set KENAN_RUNTIME_OBSERVER_BUNDLE_PATH to exercise the exchange observer.');
    fs.writeFileSync(observerBundlePath, JSON.stringify(observerBundle), { mode: 0o600 });
    console.log(`PASS: exported finalized transfer observation bundle (${included.txHash}) for the isolated DEMO ledger boundary test`);

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
