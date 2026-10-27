const assert = require('node:assert/strict');
const fs = require('node:fs');
const { ApiPromise, Keyring, WsProvider } = require('@polkadot/api');
const { cryptoWaitReady } = require('@polkadot/util-crypto');
const { WebSocket, WebSocketServer } = require('ws');

const endpoint = process.env.KENAN_RPC_WS || 'ws://127.0.0.1:9944';
const endpointUrl = new URL(endpoint);
assert(
  ['127.0.0.1', 'localhost', '[::1]'].includes(endpointUrl.hostname),
  'This integration test is restricted to a local development node.'
);
const amount1 = BigInt(process.env.KENAN_TEST_TRANSFER_BASE || '1000000000');
const amount2Override = process.env.KENAN_TEST_TRANSFER_SECOND_BASE
  ? BigInt(process.env.KENAN_TEST_TRANSFER_SECOND_BASE)
  : null;
const amount3 = BigInt(process.env.KENAN_TEST_TRANSFER_THIRD_BASE || '250000000');
const finalityRpcDelayMs = Number(process.env.KENAN_FINALITY_RPC_DELAY_MS || '2000');
assert(amount1 > 0n && (amount2Override === null || amount2Override > 0n) && amount3 > 0n, 'Transfer amounts must be positive integers.');
assert(Number.isSafeInteger(finalityRpcDelayMs) && finalityRpcDelayMs >= 0, 'Finality RPC delay must be a non-negative integer.');

function bytesToHex(bytes) {
  return `0x${Buffer.from(bytes).toString('hex')}`;
}

function errorText(error) {
  return String(error && error.message ? error.message : error);
}

function findBytes(haystack, needle) {
  outer: for (let i = 0; i <= haystack.length - needle.length; i += 1) {
    for (let j = 0; j < needle.length; j += 1) {
      if (haystack[i + j] !== needle[j]) continue outer;
    }
    return i;
  }
  return -1;
}

async function startFinalityDelayProxy(targetUrl, delayMs) {
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  const clients = new Set();
  let delayedFinalizedNotifications = 0;
  let watchedSubmissions = 0;
  server.on('connection', (client) => {
    clients.add(client);
    const upstream = new WebSocket(targetUrl);
    const queued = [];
    upstream.on('open', () => {
      for (const [data, isBinary] of queued.splice(0)) upstream.send(data, { binary: isBinary });
    });
    client.on('message', (data, isBinary) => {
      try {
        const payload = JSON.parse(data.toString());
        const messages = Array.isArray(payload) ? payload : [payload];
        watchedSubmissions += messages.filter((message) => message?.method === 'author_submitAndWatchExtrinsic').length;
      } catch {
        // Binary/non-JSON frames are forwarded unchanged and are not submission requests.
      }
      if (upstream.readyState === WebSocket.OPEN) upstream.send(data, { binary: isBinary });
      else queued.push([data, isBinary]);
    });
    upstream.on('message', (data, isBinary) => {
      let payload;
      try {
        payload = JSON.parse(data.toString());
      } catch {
        if (client.readyState === WebSocket.OPEN) client.send(data, { binary: isBinary });
        return;
      }
      const messages = Array.isArray(payload) ? payload : [payload];
      const containsFinality = messages.some((message) => {
        const result = message?.params?.result;
        return message?.method === 'author_extrinsicUpdate' &&
          result && (result.finalized !== undefined || result.Finalized !== undefined);
      });
      if (containsFinality) {
        delayedFinalizedNotifications += 1;
        setTimeout(() => {
          if (client.readyState === WebSocket.OPEN) client.send(data, { binary: isBinary });
        }, delayMs);
      } else if (client.readyState === WebSocket.OPEN) {
        client.send(data, { binary: isBinary });
      }
    });
    const closeUpstream = () => {
      clients.delete(client);
      if (upstream.readyState === WebSocket.OPEN || upstream.readyState === WebSocket.CONNECTING) upstream.close();
    };
    client.on('close', closeUpstream);
    upstream.on('close', () => {
      if (client.readyState === WebSocket.OPEN) client.close();
    });
    upstream.on('error', () => {
      if (client.readyState === WebSocket.OPEN) client.close();
    });
  });
  await new Promise((resolve, reject) => {
    server.once('listening', resolve);
    server.once('error', reject);
  });
  const address = server.address();
  assert(address && typeof address === 'object', 'Local RPC delay proxy did not bind a TCP port.');
  return {
    endpoint: `ws://127.0.0.1:${address.port}`,
    delayedCount: () => delayedFinalizedNotifications,
    watchedCount: () => watchedSubmissions,
    close: async () => {
      for (const client of clients) client.close();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

async function signTransfer(api, signer, recipient, amount, nonce) {
  const extrinsic = api.tx.balances.transferKeepAlive(recipient.address, amount.toString());
  await extrinsic.signAsync(signer, { nonce: nonce.toString(), era: 0 });
  return extrinsic;
}

async function expectRpcRejection(api, rawExtrinsic, label) {
  let rejected = false;
  let reason = '';
  try {
    await api.rpc.author.submitExtrinsic(api.createType('Extrinsic', rawExtrinsic));
  } catch (error) {
    rejected = true;
    reason = errorText(error);
  }
  assert(rejected, `${label} was unexpectedly accepted by author_submitExtrinsic.`);
  console.log(`PASS rejection: ${label}: ${reason.slice(0, 220)}`);
  return reason;
}

async function submitAndWaitFinalized(api, extrinsic) {
  let unsubscribe;
  let timeout;
  let inBlockHash = null;
  let settled = false;
  let finalizedCallbacks = 0;
  const finalizedHash = await new Promise((resolve, reject) => {
    timeout = setTimeout(() => {
      if (unsubscribe) unsubscribe();
      reject(new Error('Timed out waiting for transaction finality.'));
    }, 120_000);

    api.rpc.author
      .submitAndWatchExtrinsic(extrinsic, (status) => {
        if (status.isInBlock) inBlockHash = status.asInBlock.toHex();
        if (status.isInvalid || status.isDropped || status.isUsurped) {
          if (!settled) {
            settled = true;
            clearTimeout(timeout);
            reject(new Error(`Extrinsic was ${status.type}: ${status.toString()}`));
          }
          return;
        }
        if (status.isFinalized && !settled) {
          settled = true;
          finalizedCallbacks += 1;
          const hash = status.asFinalized.toHex();
          clearTimeout(timeout);
          resolve(hash);
        }
      })
      .then((stop) => {
        unsubscribe = stop;
        if (settled && unsubscribe) unsubscribe();
      })
      .catch((error) => {
        if (!settled) {
          settled = true;
          clearTimeout(timeout);
          reject(error);
        }
      });
  });
  if (unsubscribe) unsubscribe();
  assert.equal(finalizedCallbacks, 1, 'A transaction must produce one finalized acknowledgement.');
  assert(inBlockHash, 'Finalized transaction was not observed in an included block first.');
  return { finalizedHash, inBlockHash };
}

async function stateAt(api, blockHash, address) {
  const at = await api.at(blockHash);
  const account = await at.query.system.account(address);
  return {
    free: BigInt(account.data.free.toString()),
    nonce: BigInt(account.nonce.toString()),
  };
}

async function assertFinalizedTransferEvents(api, extrinsicHash, blockHash) {
  const block = await api.rpc.chain.getBlock(blockHash);
  const extrinsicIndex = block.block.extrinsics.findIndex((extrinsic) => extrinsic.hash.toHex() === extrinsicHash);
  assert(extrinsicIndex >= 0, `Finalized block ${blockHash} does not contain expected extrinsic ${extrinsicHash}.`);
  const records = await api.query.system.events.at(blockHash);
  const txEvents = records.filter((record) =>
    record.phase.isApplyExtrinsic && record.phase.asApplyExtrinsic.toNumber() === extrinsicIndex
  );
  assert(
    txEvents.some(({ event }) => event.section === 'system' && event.method === 'ExtrinsicSuccess'),
    `Extrinsic ${extrinsicHash} has no System.ExtrinsicSuccess event.`
  );
  assert(
    txEvents.some(({ event }) => event.section === 'balances' && event.method === 'Transfer'),
    `Extrinsic ${extrinsicHash} has no Balances.Transfer event.`
  );
}

async function main() {
  await cryptoWaitReady();
  const provider = new WsProvider(endpoint);
  const api = await ApiPromise.create({ provider, noInitWarn: true });
  try {
    const chain = (await api.rpc.system.chain()).toString();
    const genesisHash = (await api.rpc.chain.getBlockHash(0)).toHex();
    const existentialDeposit = api.consts.balances.existentialDeposit.toBigInt();
    const amount2 = amount2Override ?? existentialDeposit + 500_000_000n;
    assert(amount2 > existentialDeposit, 'Second transfer to the unendowed recipient must exceed the runtime existential deposit.');
    console.log(`Runtime existential deposit: ${existentialDeposit}; second transfer: ${amount2}`);
    const keyring = new Keyring({ type: 'sr25519' });
    const alice = keyring.addFromUri('//Alice');
    const bob = keyring.addFromUri('//Bob');
    const charlie = keyring.addFromUri('//Charlie');
    const nonceBefore = (await api.rpc.system.accountNextIndex(alice.address)).toBigInt();
    const finalizedBefore = await api.rpc.chain.getFinalizedHead();
    const aliceBefore = await stateAt(api, finalizedBefore, alice.address);
    const bobBefore = await stateAt(api, finalizedBefore, bob.address);
    const charlieBefore = await stateAt(api, finalizedBefore, charlie.address);

    const first = await signTransfer(api, alice, bob, amount1, nonceBefore);
    const firstRaw = first.toHex();
    const fullBytes = first.toU8a();
    const signatureBytes = first.signature.toU8a();
    const signatureOffset = findBytes(fullBytes, signatureBytes);
    assert(signatureOffset >= 0, 'Could not locate the signed Sr25519 signature in the encoded extrinsic.');
    const badSignature = Uint8Array.from(fullBytes);
    badSignature[signatureOffset + Math.floor(signatureBytes.length / 2)] ^= 0x01;
    await expectRpcRejection(api, bytesToHex(badSignature), 'invalid signature');

    const changedCall = Uint8Array.from(fullBytes);
    changedCall[changedCall.length - 1] ^= 0x01;
    const changedCallHex = bytesToHex(changedCall);
    const decodedChanged = api.createType('Extrinsic', changedCallHex);
    assert.notEqual(
      BigInt(decodedChanged.method.args[1].toString()),
      amount1,
      'The mutation must change the signed transfer amount.'
    );
    await expectRpcRejection(api, changedCallHex, 'mutated signed transaction payload');
    assert.equal(
      (await api.rpc.system.accountNextIndex(alice.address)).toBigInt(),
      nonceBefore,
      'Rejected invalid extrinsics must not consume the signer nonce.'
    );
    const afterRejectedHead = await api.rpc.chain.getFinalizedHead();
    assert.equal((await stateAt(api, afterRejectedHead, alice.address)).free, aliceBefore.free, 'Rejected extrinsics changed sender balance.');
    assert.equal((await stateAt(api, afterRejectedHead, bob.address)).free, bobBefore.free, 'Rejected extrinsics changed recipient balance.');
    assert.equal((await stateAt(api, afterRejectedHead, charlie.address)).free, charlieBefore.free, 'Rejected extrinsics changed unrelated balance.');
    console.log('PASS rejected transactions leave balances and nonce unchanged');

    const tx1Hash = first.hash.toHex();
    const firstResult = await submitAndWaitFinalized(api, first);
    await assertFinalizedTransferEvents(api, tx1Hash, firstResult.finalizedHash);
    const tx1After = await stateAt(api, firstResult.finalizedHash, alice.address);
    const bobAfter1 = await stateAt(api, firstResult.finalizedHash, bob.address);
    const tx1Fee = aliceBefore.free - tx1After.free - amount1;
    assert(tx1Fee > 0n, 'First sender balance did not include a positive transaction fee.');
    assert.equal(bobAfter1.free - bobBefore.free, amount1, 'First recipient balance delta did not equal the transfer amount.');
    assert.equal(tx1After.nonce, nonceBefore + 1n, 'Nonce did not increment exactly once after the first finalized transfer.');
    await expectRpcRejection(api, firstRaw, 'replay of the already-finalized transaction');
    assert.equal(
      (await api.rpc.system.accountNextIndex(alice.address)).toBigInt(),
      nonceBefore + 1n,
      'Replaying a finalized transaction must not increment nonce again.'
    );

    const staleNonceExtrinsic = await signTransfer(api, alice, charlie, 42n, nonceBefore);
    await expectRpcRejection(api, staleNonceExtrinsic.toHex(), 'new transaction signed with a stale nonce');
    const afterReplayHead = await api.rpc.chain.getFinalizedHead();
    assert.equal((await stateAt(api, afterReplayHead, alice.address)).free, tx1After.free, 'Replay or stale nonce changed sender balance.');
    assert.equal((await stateAt(api, afterReplayHead, bob.address)).free, bobAfter1.free, 'Replay or stale nonce repeated the recipient transfer.');
    assert.equal((await stateAt(api, afterReplayHead, charlie.address)).free, charlieBefore.free, 'Replay or stale nonce changed unrelated balance.');
    console.log(`PASS nonce after first transfer and replay rejection: ${nonceBefore} -> ${nonceBefore + 1n}`);

    const nonce2 = (await api.rpc.system.accountNextIndex(alice.address)).toBigInt();
    const second = await signTransfer(api, alice, charlie, amount2, nonce2);
    const tx2Hash = second.hash.toHex();
    const delayProxy = await startFinalityDelayProxy(endpoint, finalityRpcDelayMs);
    let delayedApi;
    let secondResult;
    const delayedWaitStarted = Date.now();
    try {
      delayedApi = await ApiPromise.create({ provider: new WsProvider(delayProxy.endpoint), noInitWarn: true });
      assert.equal((await delayedApi.rpc.chain.getBlockHash(0)).toHex(), genesisHash, 'Delayed RPC proxy reached a different network.');
      secondResult = await submitAndWaitFinalized(delayedApi, second);
      const delayedWaitElapsedMs = Date.now() - delayedWaitStarted;
      assert.equal(delayProxy.delayedCount(), 1, 'Expected exactly one Finalized notification to be delayed by the RPC proxy.');
      assert.equal(delayProxy.watchedCount(), 1, 'The client resubmitted or duplicated a watched transaction while finality was delayed.');
      assert(delayedWaitElapsedMs >= finalityRpcDelayMs - 100, 'Finality acknowledgement was not delayed by the configured interval.');
      console.log(`PASS delayed RPC finality: notification delayed ${finalityRpcDelayMs}ms; one submission only`);
    } finally {
      if (delayedApi) await delayedApi.disconnect();
      await delayProxy.close();
    }
    await assertFinalizedTransferEvents(api, tx2Hash, secondResult.finalizedHash);
    const aliceAfter2 = await stateAt(api, secondResult.finalizedHash, alice.address);
    const bobAfter2 = await stateAt(api, secondResult.finalizedHash, bob.address);
    const charlieAfter2 = await stateAt(api, secondResult.finalizedHash, charlie.address);
    const tx2Fee = tx1After.free - aliceAfter2.free - amount2;
    assert(tx2Fee > 0n, 'Second sender balance did not include a positive transaction fee.');
    assert.equal(bobAfter2.free, bobAfter1.free, 'Unrelated recipient balance changed during the second transfer.');
    assert.equal(charlieAfter2.free - charlieBefore.free, amount2, 'Second recipient balance delta did not equal the transfer amount.');
    assert.equal(aliceAfter2.nonce, nonce2 + 1n, 'Nonce did not increment exactly once after the second finalized transfer.');

    const nonce3 = (await api.rpc.system.accountNextIndex(alice.address)).toBigInt();
    const third = await signTransfer(api, alice, bob, amount3, nonce3);
    const tx3Hash = third.hash.toHex();
    const thirdResult = await submitAndWaitFinalized(api, third);
    await assertFinalizedTransferEvents(api, tx3Hash, thirdResult.finalizedHash);
    const aliceAfter3 = await stateAt(api, thirdResult.finalizedHash, alice.address);
    const bobAfter3 = await stateAt(api, thirdResult.finalizedHash, bob.address);
    const charlieAfter3 = await stateAt(api, thirdResult.finalizedHash, charlie.address);
    const tx3Fee = aliceAfter2.free - aliceAfter3.free - amount3;
    assert(tx3Fee > 0n, 'Third sender balance did not include a positive transaction fee.');
    assert.equal(bobAfter3.free - bobAfter2.free, amount3, 'Third recipient balance delta did not equal the transfer amount.');
    assert.equal(charlieAfter3.free, charlieAfter2.free, 'Unrelated recipient balance changed during the third transfer.');
    assert.equal(aliceAfter3.nonce, nonce3 + 1n, 'Nonce did not increment exactly once after the third finalized transfer.');
    assert.equal(
      aliceBefore.free - aliceAfter3.free,
      amount1 + amount2 + amount3 + tx1Fee + tx2Fee + tx3Fee,
      'Aggregate sender debit does not reconcile to all transfer amounts plus measured fees.'
    );
    assert.equal(bobAfter3.free - bobBefore.free, amount1 + amount3, 'Aggregate Bob balance does not reconcile to his transfers.');
    assert.equal(charlieAfter3.free - charlieBefore.free, amount2, 'Aggregate Charlie balance does not reconcile to his transfer.');

    const finalizedHead = await api.rpc.chain.getFinalizedHead();
    const finalizedHeader = await api.rpc.chain.getHeader(finalizedHead);
    const finalizedHeight = BigInt(finalizedHeader.number.toString());
    assert(finalizedHeight > 0n, 'No finalized blocks were observed.');
    const finalizedTransferHeights = await Promise.all([
      firstResult.finalizedHash,
      secondResult.finalizedHash,
      thirdResult.finalizedHash,
    ].map(async (hash) => BigInt((await api.rpc.chain.getHeader(hash)).number.toString())));
    assert(
      finalizedTransferHeights[0] < finalizedTransferHeights[1] &&
        finalizedTransferHeights[1] < finalizedTransferHeights[2],
      'Sequential finalized transfers did not advance the finalized block height.'
    );
    assert.equal(aliceAfter3.nonce, nonceBefore + 3n, 'Sequential finalized transfers did not advance nonce three times.');

    const result = {
      result: 'passed',
      suite: 'real local Polkadot SDK Solochain integration',
      chain,
      genesisHash,
      existentialDepositBase: existentialDeposit.toString(),
      finalizedHeight: finalizedHeight.toString(),
      checks: {
        invalidSignatureRejected: true,
        mutatedSignedTransactionRejected: true,
        rejectedExtrinsicsDidNotConsumeNonce: true,
        finalizedTransactionReplayRejected: true,
        staleNonceRejected: true,
        delayedFinalityRpcNotificationHandledWithoutResubmission: true,
        allThreeTransactionsPresentInFinalizedBlocks: true,
        allThreeTransactionsHaveSuccessfulTransferEvents: true,
        finalizedHeightAdvancedAcrossTransactions: true,
        sequentialTransfersAndFeesVerified: true,
      },
      accounts: {
        alice: alice.address,
        bob: bob.address,
        charlie: charlie.address,
      },
      initial: {
        aliceFree: aliceBefore.free.toString(),
        bobFree: bobBefore.free.toString(),
        charlieFree: charlieBefore.free.toString(),
        aliceNonce: nonceBefore.toString(),
      },
      transfers: [
        { txHash: tx1Hash, blockHash: firstResult.finalizedHash, amount: amount1.toString(), fee: tx1Fee.toString(), nonce: nonceBefore.toString() },
        { txHash: tx2Hash, blockHash: secondResult.finalizedHash, amount: amount2.toString(), fee: tx2Fee.toString(), nonce: nonce2.toString(), finalityRpcDelayMs },
        { txHash: tx3Hash, blockHash: thirdResult.finalizedHash, amount: amount3.toString(), fee: tx3Fee.toString(), nonce: nonce3.toString() },
      ],
      final: {
        aliceFree: aliceAfter3.free.toString(),
        aliceNonce: aliceAfter3.nonce.toString(),
        bobFree: bobAfter3.free.toString(),
        charlieFree: charlieAfter3.free.toString(),
      },
    };
    console.log(JSON.stringify(result, null, 2));

    const snapshotPath = process.env.KENAN_STATE_SNAPSHOT;
    if (snapshotPath) {
      fs.writeFileSync(snapshotPath, `${JSON.stringify({
        chain,
        genesisHash,
        finalizedHeight: finalizedHeight.toString(),
        accounts: result.accounts,
        final: result.final,
        transfers: result.transfers,
      }, null, 2)}\n`, { mode: 0o600 });
      console.log(`Wrote private local restart checkpoint: ${snapshotPath}`);
    }
  } finally {
    await api.disconnect();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
